# 05 — Phase 1: Foundation (Days 1–2)

**Goal:** the app boots (via `uvicorn` locally, or `docker compose up -d` once the
Dockerfile exists), `/api/doctor` reports green, and you can register a project and get
back a real dependency graph — with **no LLM involved yet**.

**Why no LLM yet:** if the first thing you build depends on a model, you cannot tell
whether a failure is your code or the model. Phase 1 is 100% deterministic. Everything
you build here you can trust for the next 13 days.

**Where this runs:** everything in this phase is normal Python you develop and test in
your local venv (`00-prerequisites.md` §0.4). The Dockerfile that packages it into the
image the end user actually runs comes together across every phase and is finalized in
`11-phase-7-polish-demo.md` — you do not need Docker running to do most of Phase 1 work,
only Postgres (which you can run as a bare container for dev — see the checkpoint below).

---

## Step 1.1 — Package skeleton

```bash
cd /Users/haiderbukhari/Public/DeployMint && source venv/bin/activate
```

```bash
mkdir -p deploymint/{db,schemas,core,agents,api,runner,policies,data,web/templates/partials,web/static/vendor} tests/fixtures scripts && for d in deploymint deploymint/db deploymint/schemas deploymint/core deploymint/agents deploymint/api deploymint/runner tests; do touch $d/__init__.py; done
```

Write `pyproject.toml` (full content in `02-repo-layout.md` §2.6), then:

```bash
pip install -e ".[dev]"
```

This installs the app package into your venv **for local development**. It is not how
the end user gets DeployMint — they get the built Docker image (`02-repo-layout.md`
§2.3). This command exists so you can run and test the code directly while writing it.

Set the version in `deploymint/__init__.py`:

```python
__version__ = "0.1.0"
```

**Checkpoint:** `python -c "import deploymint; print(deploymint.__version__)"` → `0.1.0`

---

## Step 1.2 — Config

```python
# deploymint/config.py
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEPLOYMINT_", extra="ignore")

    # server
    host: str = "0.0.0.0"       # fine inside a container; compose publishes 127.0.0.1 only
    port: int = 8000
    sql_echo: bool = False

    # database — injected by docker-compose.yml as DATABASE_URL (no DEPLOYMINT_ prefix,
    # it's a convention shared with other tools). Falls back to a local dev default.
    database_url_env: str = "postgresql+psycopg://deploymint:deploymint@localhost:5432/deploymint"

    # llm — see 04-agents-spec.md §4.10 and core/llm.py
    anthropic_api_key: str = ""     # required; read from env, never hardcoded
    model: str = "claude-opus-5"
    llm_timeout: int = 120

    # the sandbox root — see 01-architecture.md §1.7. Inside the container this is
    # always /workspace (the bind-mounted projects directory). Tests override it to
    # a tmp directory.
    workspace_root: Path = Path("/workspace")

    # kubernetes — optional; if the mounted kubeconfig has no reachable cluster,
    # the Execution Engine falls back to `docker run` (01-architecture.md decision 12)
    kube_context: str = ""
    rollout_timeout: int = 120

    # security
    block_severity: str = "critical"      # critical | high | medium
    enable_redteam: bool = True

    # runtime
    max_concurrent_runs: int = 2
    max_repo_files: int = 5000

    @property
    def database_url(self) -> str:
        import os
        return os.environ.get("DATABASE_URL", self.database_url_env)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Three real changes from a local-first design, all load-bearing:

1. **No `~/.deploymint` home directory.** There is nothing left that needs one —
   Postgres owns its own state (a named volume Compose manages), and generated artifacts
   live next to the project that generated them, under `workspace_root`. See
   `01-architecture.md` §1.8.
2. **`database_url` reads the plain `DATABASE_URL` env var first**, because that's the
   convention `docker-compose.yml` uses (`02-repo-layout.md` §2.4) and matches how most
   tools expect a Postgres connection string to be injected. The `DEPLOYMINT_` prefix
   still applies to everything else.
3. **`workspace_root` is configurable, not hardcoded to `/workspace`.** Inside the
   container it always is `/workspace` — but tests need to point it at a tmp directory,
   and pydantic-settings makes that a one-line env var override rather than a
   conditional in application code.

`@lru_cache` + a getter, **not** a module-level `settings = Settings()`. Tests override
`DEPLOYMINT_WORKSPACE_ROOT` and `DATABASE_URL`, then call `get_settings.cache_clear()`.
Import-time instantiation makes that impossible.

---

## Step 1.3 — Sandbox (security critical — write the tests first)

The sandbox boundary changed from "anywhere except a short list of forbidden system
paths" to "**only** under the mounted workspace root" — a strictly narrower, safer rule,
because there is now exactly one legitimate root instead of an open-ended filesystem.
See `01-architecture.md` §1.7.

```python
# deploymint/core/sandbox.py
from pathlib import Path
from deploymint.config import get_settings


class SandboxError(ValueError):
    pass


def validate_repo_path(raw: str) -> Path:
    """Resolve and validate a user-supplied path. Must live under workspace_root —
    there is no other directory the app has any legitimate reason to touch."""
    if not raw or not raw.strip():
        raise SandboxError("repo_path is empty")

    root = get_settings().workspace_root.resolve()
    p = Path(raw).expanduser()
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise SandboxError(f"path does not exist or cannot be resolved: {raw}") from e

    if not p.is_dir():
        raise SandboxError(f"not a directory: {p}")
    if not p.is_relative_to(root):
        raise SandboxError(f"path must be under {root}: {p}")
    return p


def safe_join(root: Path, relative: str) -> Path:
    """Join and assert the result stays inside root."""
    candidate = (root / relative).resolve()
    root = root.resolve()
    if not candidate.is_relative_to(root):
        raise SandboxError(f"path traversal blocked: {relative}")
    return candidate
```

```python
# tests/test_sandbox.py
import pytest
from deploymint.core.sandbox import validate_repo_path, safe_join, SandboxError
from deploymint.config import get_settings


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPLOYMINT_WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_rejects_outside_workspace():
    with pytest.raises(SandboxError):
        validate_repo_path("/etc")


def test_rejects_missing(workspace):
    with pytest.raises(SandboxError):
        validate_repo_path(str(workspace / "does-not-exist"))


def test_rejects_traversal(workspace):
    with pytest.raises(SandboxError):
        safe_join(workspace, "../../etc/passwd")


def test_accepts_dir_under_workspace(workspace):
    (workspace / "my-app").mkdir()
    assert validate_repo_path(str(workspace / "my-app")).is_dir()
```

This is simpler than the original `FORBIDDEN_ROOTS` denylist — a single allowlist rule
(`is_relative_to(workspace_root)`) covers every case the old list of forbidden system
directories was trying to approximate, and it's impossible to bypass by finding a system
path the denylist forgot.

---

## Step 1.4 — Database (Postgres)

Write `deploymint/db/models.py` and `deploymint/db/database.py` verbatim from
`03-data-model.md` §3.2.

For local dev, run a bare Postgres container (this is *not* the compose stack — just a
throwaway instance so you can develop against the real thing before the Dockerfile
exists):

```bash
docker run -d --name deploymint-dev-db -e POSTGRES_USER=deploymint -e POSTGRES_PASSWORD=deploymint -e POSTGRES_DB=deploymint -p 5432:5432 postgres:16-alpine
```

**Checkpoint:**

```bash
python -c "from deploymint.db.database import init_db, get_engine; init_db(); import sqlalchemy as sa; print(sa.inspect(get_engine()).get_table_names())"
```

Must print `['audit_logs', 'events', 'projects', 'runs']`.

---

## Step 1.5 — Event bus

Unchanged from a single-container design — one process, one in-memory queue per run is
correct and sufficient. See `01-architecture.md` §1.6 for why a Postgres pub/sub layer
would be unnecessary complexity here.

```python
# deploymint/core/events.py
import asyncio
from datetime import datetime, timezone
from typing import Any


class EventBus:
    """One per run. Fans events to live WebSocket clients and to the DB writer."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.seq = 0
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)
        self._sinks: list = []          # async callables persisted by RunManager
        self.closed = False

    def add_sink(self, fn) -> None:
        self._sinks.append(fn)

    async def emit(self, type_: str, payload: dict[str, Any] | None = None) -> dict:
        self.seq += 1
        evt = {
            "run_id": self.run_id,
            "seq": self.seq,
            "type": type_,
            "payload": payload or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        for sink in self._sinks:
            try:
                await sink(evt)
            except Exception:            # a broken sink must never kill a run
                pass
        try:
            self.queue.put_nowait(evt)
        except asyncio.QueueFull:
            pass                          # slow consumer: drop, don't block the agent
        return evt

    async def close(self) -> None:
        self.closed = True
        await self.queue.put({"type": "__end__"})


class BusRegistry:
    """Global map of run_id -> EventBus, so the WS route can find a live run."""

    def __init__(self):
        self._buses: dict[str, EventBus] = {}

    def create(self, run_id: str) -> EventBus:
        bus = EventBus(run_id)
        self._buses[run_id] = bus
        return bus

    def get(self, run_id: str) -> EventBus | None:
        return self._buses.get(run_id)

    def drop(self, run_id: str) -> None:
        self._buses.pop(run_id, None)


registry = BusRegistry()
```

The `QueueFull` drop is deliberate. A `docker build` emits log lines faster than a
browser consumes them. Blocking the agent to keep the UI in sync would make builds
crawl. Events are also persisted to Postgres, so nothing is truly lost — a refresh
replays the complete timeline.

---

## Step 1.6 — State (FROZEN)

Write `deploymint/agents/state.py` verbatim from `01-architecture.md` §1.5.

Add at the top of the file:

```python
# ⚠️  FROZEN SCHEMA — Phase 1.
# Changing a key here means touching every agent, the DB JSONB columns, and the UI.
# If you think you need a new key, add it to an existing sub-TypedDict instead.
```

---

## Step 1.7 — Architect Agent

Implement `deploymint/core/repo_scanner.py`, `deploymint/core/graph_builder.py`, and
`deploymint/agents/architect.py` per `04-agents-spec.md` §4.1. The LLM summary in §4.1b
is a Phase 2+ addition once `core/llm.py` exists — skip it for now and leave
`architecture_summary` unset.

Build it in this order and test after each — do not write all three then debug:

1. `walk_repo()` → list of files. Test on `deploymint/` itself.
2. `detect_language()` → the manifest+extension heuristic. Test on all 4 fixtures.
3. `detect_framework()`, `find_entrypoint()`, `infer_port()`.
4. `extract_imports()` with tree-sitter. **This is the only hard part.**
5. `build_graph()` + PageRank.

### tree-sitter starter — get this working standalone first

```python
from tree_sitter_language_pack import get_parser

parser = get_parser("python")
src = b"import os\nfrom .models import User\nfrom app.db import session\n"
tree = parser.parse(src)


def walk(node, out):
    if node.type in ("import_statement", "import_from_statement"):
        out.append(src[node.start_byte:node.end_byte].decode())
    for c in node.children:
        walk(c, out)


found = []
walk(tree.root_node, found)
print(found)
```

Expected: `['import os', 'from .models import User', 'from app.db import session']`

If this prints, tree-sitter works and the rest is string handling. If it errors, fix it
now — do not build around it.

**Resolving a module to a file:**

```python
def resolve_module(mod: str, current_file: Path, repo_root: Path) -> str | None:
    if mod.startswith("."):                      # relative import
        depth = len(mod) - len(mod.lstrip("."))
        base = current_file.parent
        for _ in range(depth - 1):
            base = base.parent
        rest = mod.lstrip(".").replace(".", "/")
        candidates = [base / f"{rest}.py", base / rest / "__init__.py"]
    else:                                        # absolute — may still be internal
        rest = mod.replace(".", "/")
        candidates = [repo_root / f"{rest}.py", repo_root / rest / "__init__.py"]

    for c in candidates:
        if c.exists():
            return str(c.relative_to(repo_root))
    return None      # external dependency
```

---

## Step 1.8 — Test fixtures

Create `tests/fixtures/sample_fastapi/` — a real, minimal, *runnable* app. This is your
primary demo target, so make it good.

```
tests/fixtures/sample_fastapi/
├── requirements.txt      →  fastapi==0.111.0
│                            uvicorn[standard]==0.30.1
│                            pydantic==2.7.4
├── main.py               →  imports app.routes, creates FastAPI(), /health endpoint
└── app/
    ├── __init__.py
    ├── routes.py         →  imports app.models, defines APIRouter
    ├── models.py         →  Pydantic models
    └── db.py             →  imported by both routes.py and models.py
```

`db.py` being imported by two modules is intentional — it makes PageRank produce a
non-trivial ranking, so your graph visualization has something to show.

`main.py` **must** expose `GET /health` returning `{"status":"ok"}`. Every generated
manifest's liveness probe points there. Without it, your pods fail readiness and the
demo dies at the last step.

Tests point `workspace_root` at `tests/fixtures/` itself (via the `workspace` fixture in
§1.3), so `sample_fastapi` behaves exactly like a project the end user placed under
their own `./projects` directory — the sandbox rule is exercised for real, not mocked.

---

## Step 1.9 — API: health, doctor, projects

```python
# deploymint/api/health.py
from fastapi import APIRouter
from deploymint import __version__
from deploymint.core.doctor import run_checks

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/api/doctor")
async def doctor():
    checks = await run_checks()
    return {"checks": checks, "ok": all(c["status"] != "fail" for c in checks)}
```

`deploymint/core/doctor.py` checks: Postgres reachable, `ANTHROPIC_API_KEY` present and
the API reachable (a cheap call, not a full completion), `/var/run/docker.sock` present
and responsive, `~/.kube/config` mount present (warn-only — the Execution Engine
degrades gracefully without it). Each check returns
`{"name","status":"pass|warn|fail","detail","fix"}`. This is the containerized
equivalent of the original `deploymint doctor` CLI command — now it's an endpoint the web
UI's own dashboard header can poll, since there's no separate CLI process to run it from
inside the container.

```python
# deploymint/api/projects.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from deploymint.core.sandbox import validate_repo_path, SandboxError
from deploymint.db.database import get_db
from deploymint.db.models import Project
from deploymint.schemas.project import ProjectCreate, ProjectRead
from deploymint.agents.architect import ArchitectAgent

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    try:
        path = validate_repo_path(body.repo_path)
    except SandboxError as e:
        raise HTTPException(400, str(e)) from e

    if db.query(Project).filter_by(name=body.name).first():
        raise HTTPException(409, f"project '{body.name}' already exists")

    p = Project(name=body.name, repo_path=str(path))
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.created_at.desc()).all()


@router.post("/{project_id}/analyze")
async def analyze(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")

    result = await ArchitectAgent().run({"repo_path": p.repo_path})
    analysis = result["analysis"]

    p.analysis = analysis
    p.language = analysis["language"]
    p.framework = analysis["framework"]
    p.entrypoint = analysis["entrypoint"]
    p.exposed_port = analysis["exposed_port"]
    from datetime import datetime, timezone
    p.last_analyzed_at = datetime.now(timezone.utc)
    db.commit()
    return analysis
```

---

## Step 1.10 — Server + lifespan

```python
# deploymint/server.py
from contextlib import asynccontextmanager
from fastapi import FastAPI

from deploymint import __version__
from deploymint.db.database import init_db
from deploymint.api import health, projects


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    # shutdown: cancel in-flight runs here (Phase 5)


def create_app() -> FastAPI:
    app = FastAPI(title="DeployMint", version=__version__, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(projects.router)
    return app


app = create_app()
```

Use a **factory**. Tests build isolated apps; `--reload` needs the import string. Both
work with this shape. **This module string, `deploymint.server:app`, is exactly what the
Dockerfile's `CMD` runs** (`02-repo-layout.md` §2.3) — there is no separate CLI subcommand
that wraps it; `uvicorn` is invoked directly, both in local dev and inside the container.

---

## Step 1.11 — There is no `deploymint server` CLI command in this phase

This is a deliberate omission, not something skipped. In the original local-first design,
a `deploymint` CLI both started the server *and* acted as a client — because it was the
same process the end user installed with `pip`. In this architecture, the server is
started by the Dockerfile's `CMD` (or, for local dev, directly via `uvicorn`):

```bash
# local dev — equivalent to what the Dockerfile's CMD does
uvicorn deploymint.server:app --reload --host 0.0.0.0 --port 8000
```

Add this as `make dev` in the Makefile (`02-repo-layout.md` §2.7). The `deploymint` CLI
that ships later, in `09-phase-5-orchestration.md`, is a **pure client** — it never
imports `agents/`, never touches the database, and has no reason to exist until there's a
running server for it to talk to. Building it now would be building a second thing that
does the same job the Dockerfile already does.

---

## Step 1.12 — Phase 1 acceptance test

Terminal A:

```bash
docker run -d --name deploymint-dev-db -e POSTGRES_USER=deploymint -e POSTGRES_PASSWORD=deploymint -e POSTGRES_DB=deploymint -p 5432:5432 postgres:16-alpine
```

```bash
source venv/bin/activate && DEPLOYMINT_WORKSPACE_ROOT=$(pwd)/tests/fixtures ANTHROPIC_API_KEY=sk-ant-placeholder uvicorn deploymint.server:app --reload
```

Terminal B:

```bash
curl -s localhost:8000/health
```

```bash
curl -s localhost:8000/api/doctor | python -m json.tool
```

```bash
curl -s -X POST localhost:8000/api/projects -H 'content-type: application/json' -d "{\"name\":\"sample-api\",\"repo_path\":\"$(pwd)/tests/fixtures/sample_fastapi\"}"
```

```bash
curl -s -X POST localhost:8000/api/projects/1/analyze | python -m json.tool
```

**Pass criteria — all of these:**

- `/api/doctor` reports `database: pass` (Postgres reachable)
- `POST /api/projects` returns 201 with a resolved absolute `repo_path` **under the
  configured workspace root** — and returns 400 for anything outside it, including
  something that would have been allowed by the old denylist (e.g. `/tmp`)
- `analyze` returns `language: "python"`, `framework: "fastapi"`,
  `entrypoint: "main.py"`, `exposed_port: 8000`
- `dependency_graph.nodes` has ≥ 4 entries and `links` has ≥ 3
- `critical_files[0]` is `app/db.py` (the most-imported module)
- Registering a path outside the workspace root returns HTTP 400
- `pytest -m "not slow"` is green

Tick **Phase 1** in `README.md`. Next: `06-phase-2-generation.md`.

---

## Time budget

| Task | Hours |
|---|---|
| Package skeleton, pyproject, install | 1.0 |
| Config + sandbox (workspace-root model) + tests | 1.5 |
| DB models + database.py (Postgres) | 1.5 |
| Event bus | 1.0 |
| State schema | 0.5 |
| repo_scanner (walk, detect) | 2.5 |
| tree-sitter imports + graph | 3.0 |
| Fixtures (4 sample repos) | 1.5 |
| API + server + doctor | 2.5 |
| Tests + debugging | 2.5 |
| **Total** | **~17.5 h (2 days)** |

If you run over: cut fixtures to FastAPI only, and make `extract_imports` handle Python
only. JS/Go/Java import parsing is a Phase-2 nice-to-have.
