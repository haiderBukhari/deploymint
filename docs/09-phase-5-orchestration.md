# 09 — Phase 5: LangGraph Orchestration & Live Streaming (Days 10–11)

**Goal:** replace the linear driver with a real LangGraph `StateGraph`, stream every
event to the browser over WebSocket, and route natural language commands
(`"deploy my project"`) through the same graph.

By now all five agents work standalone. This phase is **glue** — and that is exactly why
it comes late. Wiring a graph around working agents is a day; debugging a graph around
broken agents is a week.

---

## Step 5.1 — The graph

```python
# deploymint/agents/graph.py
import time
from langgraph.graph import StateGraph, END

from deploymint.agents.state import DeployState
from deploymint.agents.architect import ArchitectAgent
from deploymint.agents.smith import ArtifactSmithAgent
from deploymint.agents.warden import SecurityWardenAgent
from deploymint.agents.redteam import RedTeamAgent
from deploymint.agents.execution import ExecutionEngineAgent
from deploymint.agents.oracle import ObservabilityOracleAgent
from deploymint.agents.finops import FinOpsAgent
from deploymint.config import get_settings


def _wrap(agent):
    """Adapt a BaseAgent into a LangGraph node with timing + enter/exit events."""
    async def node(state: DeployState) -> dict:
        await agent.emit("node.enter", node=agent.name)
        t0 = time.perf_counter()
        try:
            result = await agent.run(state)
        except Exception as e:                      # a node must never kill the graph
            await agent.emit("error", node=agent.name, message=str(e)[:500])
            result = {"errors": state.get("errors", []) + [f"{agent.name}: {str(e)[:300]}"]}
        ms = int((time.perf_counter() - t0) * 1000)
        await agent.emit("node.exit", node=agent.name, ms=ms)
        return {**result, "current_node": agent.name}
    node.__name__ = f"{agent.name}_node"
    return node


def security_gate(state: DeployState) -> str:
    sec = state.get("security") or {}
    if state.get("force"):
        return "execute"
    return "execute" if sec.get("passed") else "blocked"


def post_execution(state: DeployState) -> str:
    dep = state.get("deployment") or {}
    return "observe" if dep.get("status") == "running" else "finops"


async def blocked_node(state: DeployState) -> dict:
    return {"current_node": "blocked"}


def build_graph(bus=None, *, skip_deploy: bool = False):
    s = get_settings()

    g = StateGraph(DeployState)
    g.add_node("architect", _wrap(ArchitectAgent(bus)))
    g.add_node("smith", _wrap(ArtifactSmithAgent(bus)))
    g.add_node("warden", _wrap(SecurityWardenAgent(bus)))
    g.add_node("blocked", blocked_node)
    g.add_node("finops", _wrap(FinOpsAgent(bus)))

    g.set_entry_point("architect")
    g.add_edge("architect", "smith")
    g.add_edge("smith", "warden")

    if s.enable_redteam:
        g.add_node("redteam", _wrap(RedTeamAgent(bus)))
        g.add_edge("warden", "redteam")
        gate_source = "redteam"
    else:
        gate_source = "warden"

    if skip_deploy:
        g.add_conditional_edges(gate_source, security_gate,
                                {"execute": "finops", "blocked": "blocked"})
    else:
        g.add_node("execution", _wrap(ExecutionEngineAgent(bus)))
        g.add_node("oracle", _wrap(ObservabilityOracleAgent(bus)))
        g.add_conditional_edges(gate_source, security_gate,
                                {"execute": "execution", "blocked": "blocked"})
        g.add_conditional_edges("execution", post_execution,
                                {"observe": "oracle", "finops": "finops"})
        g.add_edge("oracle", "finops")

    g.add_edge("finops", END)
    g.add_edge("blocked", END)
    return g.compile()
```

### Things that will bite you

**1. `DeployState` is a `TypedDict` with `NotRequired` keys.** LangGraph merges the dict
each node returns into the state. A node returning `{"errors": [...]}` **replaces**
`errors` — it does not append. That is why every agent does
`state.get("errors", []) + [new]` explicitly. If you want true append semantics, use an
`Annotated[list, operator.add]` reducer — but explicit concatenation is simpler and you
can see exactly what happens.

**2. Node names must be unique strings** and cannot collide with `END`.

**3. Conditional edge mapping keys are the *return values* of your condition function,**
not node names. `security_gate` returns `"execute"`/`"blocked"`; the dict maps those to
node names. Getting this backwards produces a confusing `KeyError` at compile time.

**4. `skip_deploy` is not a nicety.** During Phase 5–6 development you will run the
pipeline dozens of times. Each full run is ~90 s with a docker build. `skip_deploy=True`
makes it ~20 s. Wire it into `RunCreate` and the CLI (`--no-deploy`).

---

## Step 5.2 — Streaming

```python
# deploymint/runner/manager.py
import asyncio, time, uuid
from datetime import datetime, timezone

from deploymint.agents.graph import build_graph
from deploymint.core.events import registry
from deploymint.db.database import get_session_factory
from deploymint.db.models import Run, Event, Project
from deploymint.config import get_settings

_semaphore: asyncio.Semaphore | None = None
_tasks: dict[str, asyncio.Task] = {}


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().max_concurrent_runs)
    return _semaphore


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


async def start_run(project: Project, *, force=False, trigger="api", skip_deploy=False) -> str:
    run_id = new_run_id()
    Session = get_session_factory()
    with Session() as db:
        db.add(Run(id=run_id, project_id=project.id, status="pending",
                   trigger=trigger, force=force, errors=[]))
        db.commit()

    bus = registry.create(run_id)

    async def persist(evt: dict):
        with Session() as db:
            db.add(Event(run_id=evt["run_id"], seq=evt["seq"],
                         type=evt["type"], payload=evt["payload"]))
            db.commit()

    bus.add_sink(persist)
    _tasks[run_id] = asyncio.create_task(
        _execute(run_id, project.id, project.name, project.repo_path,
                 force, skip_deploy, bus))
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
            _tasks.pop(run_id, None)


def _final_status(state: dict) -> str:
    sec = state.get("security") or {}
    dep = state.get("deployment") or {}
    if state.get("current_node") == "blocked" or (sec.get("passed") is False and not state.get("force")):
        return "blocked"
    if dep.get("status") == "failed":
        return "failed"
    if state.get("errors") and not dep.get("status") == "running":
        return "failed" if not state.get("artifacts") else "success"
    return "success"


async def cancel_run(run_id: str) -> bool:
    task = _tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
```

`stream_mode="values"` yields the full accumulated state after each node — perfect for
persisting progress. `stream_mode="updates"` yields only the delta; use that if state
gets large. For the MVP, `values` is simpler and the state is small.

---

## Step 5.3 — WebSocket

```python
# deploymint/api/ws.py
import asyncio, json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from deploymint.core.events import registry
from deploymint.db.database import get_session_factory
from deploymint.db.models import Event, Run

router = APIRouter()
TERMINAL = {"success", "failed", "blocked", "cancelled"}


@router.websocket("/ws/runs/{run_id}")
async def stream_run(ws: WebSocket, run_id: str):
    await ws.accept()
    Session = get_session_factory()

    since = 0
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
        since = int(first.get("since", 0))
    except (asyncio.TimeoutError, Exception):
        since = 0

    # 1. replay persisted events
    with Session() as db:
        rows = (db.query(Event).filter(Event.run_id == run_id, Event.seq > since)
                .order_by(Event.seq).all())
        for r in rows:
            await ws.send_json({"seq": r.seq, "type": r.type,
                                "payload": r.payload, "ts": r.ts.isoformat()})
        run = db.get(Run, run_id)

    # 2. if the run already finished, close
    if run and run.status in TERMINAL:
        await ws.send_json({"seq": -1, "type": "run.end",
                            "payload": {"status": run.status}})
        await ws.close()
        return

    # 3. tail the live queue
    bus = registry.get(run_id)
    if not bus:
        await ws.close()
        return

    try:
        while True:
            evt = await bus.queue.get()
            if evt.get("type") == "__end__":
                break
            if evt["seq"] > since:
                await ws.send_json(evt)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
```

### The multi-client problem

A single `asyncio.Queue` supports **one** consumer. Two browser tabs on the same run
each steal half the events. For the MVP that is acceptable (document it). The clean fix
is per-client queues:

```python
class EventBus:
    def __init__(self, run_id):
        self._clients: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=10_000)
        self._clients.append(q)
        return q

    def unsubscribe(self, q):
        if q in self._clients:
            self._clients.remove(q)

    async def emit(self, type_, payload=None):
        ...
        for q in self._clients:
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass
```

**Do this now.** It is 15 lines and prevents a baffling bug during a demo where someone
has two tabs open.

### Batch the log flood

`execution.log` fires hundreds of times per second during a docker build. Batch on the
client, or on the server:

```python
buffer, last_flush = [], time.monotonic()
# accumulate execution.log events; flush as one frame every 100ms or 50 lines
```

Without this the WebSocket becomes the bottleneck and the browser tab pegs a CPU core.

---

## Step 5.4 — Natural language router

```python
# deploymint/api/chat.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from deploymint.core import llm, prompts
from deploymint.db.database import get_db
from deploymint.db.models import Project
from deploymint.runner.manager import start_run

router = APIRouter(prefix="/api/chat", tags=["chat"])

KEYWORDS = {
    "deploy":   ["deploy", "ship", "launch", "release", "push live", " up "],
    "analyze":  ["analyze", "scan", "inspect", "understand", "graph", "look at"],
    "status":   ["status", "how is", "running", "health", "what's up"],
    "cost":     ["cost", "spend", "bill", "expensive", "price", "budget", "$"],
    "rollback": ["rollback", "revert", "undo", "back out"],
}


def keyword_intent(msg: str) -> tuple[str, float]:
    low = f" {msg.lower()} "
    for intent, words in KEYWORDS.items():
        if any(w in low for w in words):
            return intent, 0.6
    return "unknown", 0.0


async def classify(msg: str) -> dict:
    try:
        data = await llm.complete_json(prompts.INTENT_SYSTEM, msg, timeout=30)
        if data.get("intent") in {*KEYWORDS, "explain", "help", "unknown"}:
            return data
    except Exception:
        pass
    intent, conf = keyword_intent(msg)
    return {"intent": intent, "project": None, "params": {}, "confidence": conf}


@router.post("")
async def chat(body: dict, db: Session = Depends(get_db)):
    msg = (body.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "message is required")

    parsed = await classify(msg)
    intent = parsed["intent"]
    project = _resolve_project(db, parsed.get("project"), body.get("project_id"))

    if intent == "deploy":
        if not project:
            return {"intent": intent, "reply":
                    "Which project should I deploy? " + _project_list(db),
                    "action_taken": "none", "run_id": None}
        if parsed.get("confidence", 0) < 0.8:
            return {"intent": intent,
                    "reply": f"I think you want to deploy **{project.name}**. Confirm?",
                    "action_taken": "confirm_required", "run_id": None,
                    "pending": {"intent": "deploy", "project_id": project.id}}
        run_id = await start_run(project, trigger="chat",
                                 force=bool(parsed.get("params", {}).get("force")))
        return {"intent": intent,
                "reply": f"Deploying **{project.name}**. Watch it live: /runs/{run_id}",
                "action_taken": "run_started", "run_id": run_id}
    ...
```

**Confirmation below 0.8 confidence is not optional.** An agent that starts a deployment
because it misread "don't deploy yet" is a worse product than one that asks. This is a
one-line check that demonstrates real judgment about agent safety — mention it.

---

## Step 5.5 — The thin CLI client

This is the piece flagged as deferred back in `02-repo-layout.md` §2.1 and
`05-phase-1-foundation.md` §1.11 — it's built now because this is the first point where
there's a running, fully-functional server for it to talk to. **It is a separate,
minimal package from the app itself**, not a Click command bolted onto
`deploymint/cli.py` inside the container. It needs `click`, `httpx`, `websockets`,
`rich` — nothing from `agents/`, `core/`, or `db/`, because it never runs any of that
code; the container already running via `docker compose up -d` does.

```python
# cli/deploymint_cli/__init__.py — a genuinely separate package, `pip install deploymint-cli`
import sys
import click
import httpx
import websockets
import asyncio
from rich.live import Live
from rich.table import Table
from rich.console import Console

console = Console()
NODES = ["architect", "smith", "warden", "redteam", "execution", "oracle", "finops"]


@click.command()
@click.argument("path")
@click.option("--name", default=None)
@click.option("--force", is_flag=True, help="deploy even if security checks fail")
@click.option("--no-deploy", is_flag=True, help="generate + scan only")
@click.option("--server", "server_url", default="http://localhost:8000",
             envvar="DEPLOYMINT_SERVER")
def up(path, name, force, no_deploy, server_url):
    """Register PATH with a running DeployMint container and deploy it."""
    try:
        httpx.get(f"{server_url}/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        console.print(f"[red]Cannot reach DeployMint at {server_url}.[/] "
                      "Is it running? Try: [cyan]docker compose up -d[/]")
        sys.exit(3)

    project_name = name or path.strip("/").split("/")[-1]
    r = httpx.post(f"{server_url}/api/projects", json={"name": project_name, "repo_path": path})
    if r.status_code == 409:
        r = httpx.get(f"{server_url}/api/projects", params={"name": project_name})
        project_id = next(p["id"] for p in r.json() if p["name"] == project_name)
    else:
        r.raise_for_status()
        project_id = r.json()["id"]

    r = httpx.post(f"{server_url}/api/projects/{project_id}/runs",
                   json={"force": force, "skip_deploy": no_deploy, "trigger": "cli"})
    run_id = r.json()["run_id"]

    exit_code = asyncio.run(_stream(server_url, run_id))
    sys.exit(exit_code)


async def _stream(server_url: str, run_id: str) -> int:
    ws_url = server_url.replace("http", "ws") + f"/ws/runs/{run_id}"
    status_by_node, log_tail, final_status = {}, [], "running"

    async with websockets.connect(ws_url) as ws:
        await ws.send('{"since": 0}')
        with Live(_render(status_by_node, log_tail), console=console, refresh_per_second=4) as live:
            async for raw in ws:
                import json
                evt = json.loads(raw)
                if evt["type"] == "node.enter":
                    status_by_node[evt["payload"]["node"]] = "running"
                elif evt["type"] == "node.exit":
                    status_by_node[evt["payload"]["node"]] = "done"
                elif evt["type"] == "execution.log":
                    log_tail.append(evt["payload"]["line"])
                    log_tail[:] = log_tail[-15:]
                elif evt["type"] == "run.end":
                    final_status = evt["payload"]["status"]
                live.update(_render(status_by_node, log_tail))

    console.print(f"\n[bold]Result:[/] {final_status}")
    return {"success": 0, "blocked": 2}.get(final_status, 1)


def _render(status_by_node: dict, log_tail: list[str]):
    table = Table(show_header=False)
    for n in NODES:
        icon = {"running": "◐", "done": "✓"}.get(status_by_node.get(n), "○")
        table.add_row(icon, n)
    return table
```

The `httpx.get(f"{server_url}/health", ...)` check with the `docker compose up -d` hint
in the failure message is the CLI's entire relationship with "starting the server" — it
never starts anything itself, because starting DeployMint is `docker compose up -d`, a
command this package has no reason to know about beyond suggesting it.

**Distinct exit codes matter**: they make DeployMint usable inside a CI pipeline, which
is a real answer to "how does this fit my workflow?" `0` on success, `2` on blocked,
`1` on any other failure, `3` if the server itself is unreachable.

### Packaging the CLI

Ship it as its own tiny `pip install deploymint-cli` package (its own `pyproject.toml`,
in a `cli/` directory at the repo root, alongside — not inside — `deploymint/`). This is
optional polish, not required for the product to work: the web dashboard is the primary
interface (`01-architecture.md` §1.4 decision 13), and `docker compose exec app python -m
deploymint_cli up /workspace/my-app` works identically without a separate install if you
want to skip building it as a distinct package for the MVP.

---

## Step 5.6 — Phase 5 acceptance test

```bash
pytest tests/test_graph.py -v
```

```bash
deploymint up ./projects/sample-api --name sample-api
```

```bash
curl -s -X POST localhost:8000/api/chat -H 'content-type: application/json' -d '{"message":"deploy my sample-api project"}'
```

```bash
deploymint up ./projects/poisoned --name poisoned; echo "exit=$?"
```

**Pass criteria:**

- `deploymint up` streams live progress against the already-running container and ends
  with a running pod (or a running docker-run container — see `08-phase-4-execution.md`)
- Poisoned repo exits with code `2` and prints the block reason
- Chat `"deploy my sample-api project"` starts a real run and returns its `run_id`
- Chat `"which project should I deploy"` asks rather than acting
- Two browser tabs on the same run both receive the full event stream
- A browser refresh mid-run replays the timeline from `seq=0` with nothing missing
- `POST /api/runs/{id}/cancel` actually stops an in-flight run
- With `ANTHROPIC_API_KEY` unset, chat still routes via keywords and the graph still
  completes (resilience, not an offline mode — see `04-agents-spec.md` §4.10)
- Stopping the container (`docker compose down`) and running `deploymint up` against it
  prints the "is it running? try docker compose up -d" message and exits `3`

Tick **Phase 5**. Next: `10-phase-6-finops-ui.md`.

---

## Time budget

| Task | Hours |
|---|---|
| LangGraph assembly + node wrapper | 2.5 |
| RunManager rewrite for astream | 2.5 |
| WebSocket + replay + multi-client | 3.0 |
| Log batching | 1.0 |
| NL router + keyword fallback + confirmation | 3.0 |
| CLI `up` with Rich live rendering | 3.0 |
| Tests + debugging | 3.0 |
| **Total** | **~18 h (2 days)** |
