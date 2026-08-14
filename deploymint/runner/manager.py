"""Run orchestration via a LangGraph StateGraph. See docs/09-phase-5-orchestration.md."""

import asyncio
import time
import uuid
from datetime import datetime, timezone

from deploymint.agents.graph import build_graph
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


async def start_run(project: Project, *, force=False, trigger="api", skip_deploy=False) -> str:
    run_id = new_run_id()
    Session = get_session_factory()
    with Session() as db:
        db.add(Run(id=run_id, project_id=project.id, status="pending", trigger=trigger,
                   force=force, errors=[]))
        db.commit()

    bus = registry.create(run_id)

    async def persist(evt: dict):
        with Session() as db:
            db.add(Event(run_id=evt["run_id"], seq=evt["seq"], type=evt["type"],
                         payload=evt["payload"]))
            db.commit()

    bus.add_sink(persist)
    _tasks[run_id] = asyncio.create_task(
        _execute(run_id, project.id, project.name, project.repo_path, force, skip_deploy, bus)
    )
    return run_id


async def _execute(run_id, project_id, name, repo_path, force, skip_deploy, bus):
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
        }
        final = state

        try:
            graph = build_graph(bus, skip_deploy=skip_deploy)
            async for chunk in graph.astream(state, stream_mode="values"):
                final = chunk
                with Session() as db:
                    db.query(Run).filter_by(id=run_id).update(
                        {"current_node": chunk.get("current_node", "")})
                    db.commit()
            status = _final_status(final)

        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as e:
            status = "failed"
            final["errors"] = final.get("errors", []) + [f"graph: {str(e)[:300]}"]
        finally:
            ms = int((time.perf_counter() - t0) * 1000)
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
                    "completed_at": datetime.now(timezone.utc),
                })
                db.commit()
            await bus.emit("run.end", {"status": status, "duration_ms": ms})
            await bus.close()
            registry.drop(run_id)
            _tasks.pop(run_id, None)


def _final_status(state: dict) -> str:
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


async def cancel_run(run_id: str) -> bool:
    task = _tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
