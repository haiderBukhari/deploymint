"""Run orchestration via a LangGraph StateGraph. See docs/09-phase-5-orchestration.md."""

import asyncio
import time
import uuid
from datetime import UTC, datetime

from deploymint.agents.execution import ExecutionEngineAgent
from deploymint.agents.finops import FinOpsAgent
from deploymint.agents.graph import build_graph, post_execution, security_gate
from deploymint.agents.image_scan import ImageScanAgent
from deploymint.agents.oracle import ObservabilityOracleAgent
from deploymint.agents.redteam import RedTeamAgent
from deploymint.agents.smith import ArtifactSmithAgent
from deploymint.agents.warden import SecurityWardenAgent
from deploymint.config import get_settings
from deploymint.core.events import registry
from deploymint.db.database import get_session_factory
from deploymint.db.models import Event, Project, Run

_semaphore: asyncio.Semaphore | None = None
_tasks: dict[str, asyncio.Task] = {}


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        from deploymint.config import get_settings

        _semaphore = asyncio.Semaphore(get_settings().max_concurrent_runs)
    return _semaphore


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


async def start_run(project: Project, *, force=False, trigger="api", skip_deploy=False,
                    stop_after_architect=False) -> str:
    run_id = new_run_id()
    Session = get_session_factory()
    with Session() as db:
        db.add(Run(id=run_id, project_id=project.id, status="pending", trigger=trigger,
                   force=force, errors=[]))
        db.commit()

    bus = registry.create(run_id)
    bus.add_sink(_event_persist_sink)
    _tasks[run_id] = asyncio.create_task(
        _execute(run_id, project.id, project.name, project.repo_path, force, skip_deploy,
                bus, project.cloud_provider, stop_after_architect)
    )
    return run_id


async def _event_persist_sink(evt: dict):
    Session = get_session_factory()
    with Session() as db:
        db.add(Event(run_id=evt["run_id"], seq=evt["seq"], type=evt["type"],
                     payload=evt["payload"]))
        db.commit()


async def _execute(run_id, project_id, name, repo_path, force, skip_deploy, bus,
                   cloud_provider="aws", stop_after_architect=False):
    Session = get_session_factory()
    t0 = time.perf_counter()

    async with _sem():
        with Session() as db:
            db.query(Run).filter_by(id=run_id).update({"status": "running"})
            db.commit()

        await bus.emit("run.start", {"project_name": name, "repo_path": repo_path})

        state = {
            "run_id": run_id, "project_id": project_id, "project_name": name,
            "repo_path": repo_path, "force": force, "errors": [], "current_node": "",
            "cloud_provider": cloud_provider,
        }
        final = state

        try:
            graph = build_graph(bus, skip_deploy=skip_deploy,
                               stop_after_architect=stop_after_architect)
            async for chunk in graph.astream(state, stream_mode="values"):
                final = chunk
                with Session() as db:
                    db.query(Run).filter_by(id=run_id).update(
                        {"current_node": chunk.get("current_node", "")})
                    db.commit()
            status = _final_status(final, stop_after_architect=stop_after_architect)

        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as e:
            status = "failed"
            final["errors"] = final.get("errors", []) + [f"graph: {str(e)[:300]}"]
        finally:
            await _persist_final(run_id, status, final, bus, t0)


async def _persist_final(run_id: str, status: str, final: dict, bus, t0: float):
    """Shared terminal-state bookkeeping for both a fresh run (_execute,
    driven by graph.astream) and a resumed one (_resume_from_smith, driven by
    a plain sequential call chain) — same JSONB columns, same bus-close/
    registry-drop lifecycle either way. See docs/33-deploy-lock-and-findings.md."""
    ms = int((time.perf_counter() - t0) * 1000)
    Session = get_session_factory()
    with Session() as db:
        db.query(Run).filter_by(id=run_id).update({
            "status": status,
            "analysis": final.get("analysis"),
            "artifacts": final.get("artifacts"),
            "security": final.get("security"),
            "deployment": final.get("deployment"),
            "cost": final.get("cost"),
            "errors": final.get("errors", []),
            "model_used": (final.get("artifacts") or {}).get("model_used"),
            "duration_ms": ms,
            "completed_at": datetime.now(UTC),
        })
        db.commit()
    await bus.emit("run.end", {"status": status, "duration_ms": ms})
    await bus.close()
    registry.drop(run_id)
    _tasks.pop(run_id, None)


def _final_status(state: dict, *, stop_after_architect: bool = False) -> str:
    if stop_after_architect:
        # The architect phase genuinely completed — this isn't a failure,
        # there's just no more work queued yet until the user approves a
        # plan. Without this branch, the fallback below ("success" if
        # artifacts else "failed") would wrongly call this run "failed"
        # since an architect-only run has no artifacts.
        return "awaiting_approval"
    sec = state.get("security") or {}
    dep = state.get("deployment") or {}
    if state.get("current_node") == "blocked" or (
        sec.get("passed") is False and not state.get("force")
    ):
        return "blocked"
    if dep.get("status") in ("failed", "rolled_back"):
        # A rollback means the system self-healed, but the deploy did not end
        # up healthy — the run is "failed" at the top level; dep.status keeps
        # the richer "rolled_back" detail plus dep.remediation for the reader.
        return "failed"
    if dep.get("status") == "running":
        return "success"
    # skip_deploy path: no Execution Engine node ran at all
    return "success" if state.get("artifacts") else "failed"


async def resume_from_approval(run: Run, project: Project, approved_plan: dict) -> None:
    """Resumes an `awaiting_approval` run from Smith onward, bound to the
    approved knobs. Deliberately NOT a LangGraph resume/interrupt — this repo
    uses LangGraph at its simplest (no checkpointer, `astream()` takes no
    config), and the fire-and-forget task's `finally` block in `_execute`
    unconditionally tears down the bus/registry entry the moment a graph
    finishes, which a real pause would fight. Instead this hand-builds a
    DeployState from the persisted Run row and drives each remaining agent
    directly in sequence — the same precedent `api/fixes.py`'s
    `apply_finding_fix` already uses for a partial pipeline re-run. See
    docs/33-deploy-lock-and-findings.md."""
    bus = registry.create(run.id)
    bus.add_sink(_event_persist_sink)

    Session = get_session_factory()
    with Session() as db:
        db.query(Run).filter_by(id=run.id).update(
            {"status": "running", "approved_plan": approved_plan})
        db.commit()

    state = {
        "run_id": run.id, "project_id": project.id, "project_name": project.name,
        "repo_path": project.repo_path, "force": run.force,
        "errors": list(run.errors or []), "current_node": "architect",
        "cloud_provider": approved_plan.get("cloud_provider", project.cloud_provider),
        "analysis": run.analysis or {}, "approved_plan": approved_plan,
    }
    _tasks[run.id] = asyncio.create_task(_resume_execute(run.id, state, bus))


async def _resume_execute(run_id: str, state: dict, bus) -> None:
    t0 = time.perf_counter()
    async with _sem():
        await bus.emit("run.start", {"project_name": state["project_name"],
                                     "repo_path": state["repo_path"]})
        final = state
        try:
            final = await _run_from_smith(state, bus)
            status = _final_status(final)
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as e:
            status = "failed"
            final["errors"] = final.get("errors", []) + [f"resume: {str(e)[:300]}"]
        finally:
            await _persist_final(run_id, status, final, bus, t0)


async def _run_from_smith(state: dict, bus) -> dict:
    """The same agent sequence build_graph() wires (smith -> warden ->
    [redteam] -> gate -> execution -> [image_scan] -> [oracle] -> finops), as
    a plain function instead of a compiled StateGraph — see
    resume_from_approval()'s docstring for why."""
    s = get_settings()

    async def step(agent) -> None:
        await agent.emit("node.enter", node=agent.name)
        t0 = time.perf_counter()
        try:
            result = await agent.run(state)
        except Exception as e:
            await agent.emit("error", node=agent.name, message=str(e)[:500])
            result = {"errors": state.get("errors", []) + [f"{agent.name}: {str(e)[:300]}"]}
        state.update(result)
        state["current_node"] = agent.name
        ms = int((time.perf_counter() - t0) * 1000)
        await agent.emit("node.exit", node=agent.name, ms=ms)
        with_db_current_node(state.get("run_id"), agent.name)

    def with_db_current_node(run_id, node):
        Session = get_session_factory()
        with Session() as db:
            db.query(Run).filter_by(id=run_id).update({"current_node": node})
            db.commit()

    await step(ArtifactSmithAgent(bus))
    await step(SecurityWardenAgent(bus))

    if s.enable_redteam:
        await step(RedTeamAgent(bus))

    route = security_gate(state)
    if route == "blocked":
        state["current_node"] = "blocked"
        return state

    await step(ExecutionEngineAgent(bus))
    if s.enable_trivy:
        await step(ImageScanAgent(bus))

    if post_execution(state) == "observe":
        await step(ObservabilityOracleAgent(bus))

    await step(FinOpsAgent(bus))
    return state


async def cancel_run(run_id: str) -> bool:
    task = _tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
