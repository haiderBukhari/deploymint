"""Run orchestration. This is the Phase 2 LINEAR driver — Phase 5 replaces the
body of _execute() with a LangGraph StateGraph; the public start_run/cancel_run
surface stays the same. See docs/06-phase-2-generation.md §2.7 and
docs/09-phase-5-orchestration.md."""

import asyncio
import time
import uuid
from datetime import datetime, timezone

from deploymint.agents.architect import ArchitectAgent
from deploymint.agents.execution import ExecutionEngineAgent
from deploymint.agents.redteam import RedTeamAgent
from deploymint.agents.smith import ArtifactSmithAgent
from deploymint.agents.warden import SecurityWardenAgent
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

        try:
            agents = (ArchitectAgent(bus), ArtifactSmithAgent(bus),
                      SecurityWardenAgent(bus), RedTeamAgent(bus))
            for agent in agents:
                state["current_node"] = agent.name
                await bus.emit("node.enter", {"node": agent.name})
                node_t0 = time.perf_counter()
                partial = await agent.run(state)
                state.update(partial)
                await bus.emit(
                    "node.exit", {"node": agent.name, "ms": int((time.perf_counter() - node_t0) * 1000)}
                )
                with Session() as db:
                    db.query(Run).filter_by(id=run_id).update({"current_node": agent.name})
                    db.commit()

            security = state.get("security") or {}
            gate_passed = security.get("passed", False)
            if not gate_passed and not force:
                status = "blocked"
            else:
                if not gate_passed and force:
                    await bus.emit("warden.forced", {"reason": security.get("blocked_reason")})

                if skip_deploy or not state.get("artifacts"):
                    status = "success" if state.get("artifacts") else "failed"
                else:
                    agent = ExecutionEngineAgent(bus)
                    state["current_node"] = agent.name
                    await bus.emit("node.enter", {"node": agent.name})
                    partial = await agent.run(state)
                    state.update(partial)
                    await bus.emit("node.exit", {"node": agent.name})
                    with Session() as db:
                        db.query(Run).filter_by(id=run_id).update({"current_node": agent.name})
                        db.commit()
                    status = "success" if (state.get("deployment") or {}).get("status") == "running" else "failed"

        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as e:
            status = "failed"
            state["errors"] = state.get("errors", []) + [f"runner: {str(e)[:300]}"]
        finally:
            ms = int((time.perf_counter() - t0) * 1000)
            with Session() as db:
                db.query(Run).filter_by(id=run_id).update({
                    "status": status,
                    "analysis": state.get("analysis"),
                    "artifacts": state.get("artifacts"),
                    "security": state.get("security"),
                    "deployment": state.get("deployment"),
                    "cost": state.get("cost"),
                    "errors": state.get("errors", []),
                    "model_used": (state.get("artifacts") or {}).get("model_used"),
                    "duration_ms": ms,
                    "completed_at": datetime.now(timezone.utc),
                })
                db.commit()
            await bus.emit("run.end", {"status": status, "duration_ms": ms})
            await bus.close()
            registry.drop(run_id)
            _tasks.pop(run_id, None)


async def cancel_run(run_id: str) -> bool:
    task = _tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
