# 05 — Phase 1: Foundation (Days 1–2)

**Goal:** `deploymint server` boots, `deploymint doctor` is all green, and you can
register a project and get back a real dependency graph — with **no LLM involved**.

**Why no LLM yet:** if the first thing you build depends on a model, you cannot tell
whether a failure is your code or the model. Phase 1 is 100% deterministic. Everything
you build here you can trust for the next 13 days.

---

## Step 1.1 — Package skeleton

```bash
cd /Users/haiderbukhari/Public/DeployMint && source venv/bin/activate
```

```bash
mkdir -p deploymint/{db,schemas,core,agents,api,runner,policies,data,web/templates/partials,web/static/vendor} tests/fixtures scripts && for d in deploymint deploymint/db deploymint/schemas deploymint/core deploymint/agents deploymint/api deploymint/runner tests; do touch $d/__init__.py; done
```

Write `pyproject.toml` (full content in `02-repo-layout.md` §2.2), then:

```bash
pip install -e ".[dev]"
```

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

    home: Path = Path.home() / ".deploymint"

    # server
    host: str = "127.0.0.1"
    port: int = 8000
    sql_echo: bool = False

    # llm
    ollama_base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    llm_timeout: int = 180
    llm_temperature: float = 0.1

    # kubernetes
    kube_context: str = "kind-deploymint"
    kind_cluster: str = "deploymint"
    rollout_timeout: int = 120

    # security
    block_severity: str = "critical"      # critical | high | medium
    enable_redteam: bool = True

    # runtime
    max_concurrent_runs: int = 2
    max_repo_files: int = 5000

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.home / 'deploymint.db'}"

    @property
    def artifacts_dir(self) -> Path:
        return self.home / "artifacts"

    @property
    def sessions_dir(self) -> Path:
        return self.home / "sessions"

    def ensure_dirs(self) -> None:
        for d in (self.home, self.artifacts_dir, self.sessions_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
```

`@lru_cache` + a getter, **not** a module-level `settings = Settings()`. Tests need to
override `DEPLOYMINT_HOME` and call `get_settings.cache_clear()`. Import-time
instantiation makes that impossible and every test writes to your real database.

---

## Step 1.3 — Sandbox (security critical — write the tests first)

```python
# deploymint/core/sandbox.py
from pathlib import Path

FORBIDDEN_ROOTS = {
    Path("/"), Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
    Path("/var"), Path("/System"), Path("/Library"), Path.home(),
}


class SandboxError(ValueError):
    pass


def validate_repo_path(raw: str) -> Path:
    """Resolve and validate a user-supplied repository path."""
    if not raw or not raw.strip():
        raise SandboxError("repo_path is empty")

    p = Path(raw).expanduser()
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise SandboxError(f"path does not exist or cannot be resolved: {raw}") from e

    if not p.is_dir():
        raise SandboxError(f"not a directory: {p}")
    if p in FORBIDDEN_ROOTS:
        raise SandboxError(f"refusing to operate on system directory: {p}")
    if len(p.parts) < 3:
        raise SandboxError(f"path is too close to the filesystem root: {p}")
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


def test_rejects_root():
    with pytest.raises(SandboxError):
        validate_repo_path("/")


def test_rejects_missing():
    with pytest.raises(SandboxError):
        validate_repo_path("/nope/does/not/exist")


def test_rejects_traversal(tmp_path):
    with pytest.raises(SandboxError):
        safe_join(tmp_path, "../../etc/passwd")


def test_accepts_real_dir(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert validate_repo_path(str(tmp_path / "a" / "b")).is_dir()
```

---

## Step 1.4 — Database

Write `deploymint/db/models.py` and `deploymint/db/database.py` verbatim from
`03-data-model.md` §3.2.

**Checkpoint:**

```bash
python -c "from deploymint.db.database import init_db, get_engine; init_db(); import sqlalchemy as sa; print(sa.inspect(get_engine()).get_table_names())"
```

Must print `['audit_logs', 'events', 'projects', 'runs']`.

---

## Step 1.5 — Event bus

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
crawl. Events are also persisted to the DB, so nothing is truly lost — a refresh replays
the complete timeline.

---

## Step 1.6 — State (FROZEN)

Write `deploymint/agents/state.py` verbatim from `01-architecture.md` §1.5.

Add at the top of the file:

```python
# ⚠️  FROZEN SCHEMA — Phase 1.
# Changing a key here means touching every agent, the DB JSON columns, and the UI.
# If you think you need a new key, add it to an existing sub-TypedDict instead.
```

---

## Step 1.7 — Architect Agent

Implement `deploymint/core/repo_scanner.py`, `deploymint/core/graph_builder.py`, and
`deploymint/agents/architect.py` per `04-agents-spec.md` §4.1.

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
def doctor():
    checks = run_checks()
    return {"checks": checks, "ok": all(c["status"] != "fail" for c in checks)}
```

`deploymint/core/doctor.py` implements the check table from `00-prerequisites.md` §0.6.
Each check returns `{"name","status":"pass|warn|fail","detail","fix"}`.

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
from deploymint.config import get_settings
from deploymint.db.database import init_db
from deploymint.api import health, projects


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().ensure_dirs()
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
work with this shape.

---

## Step 1.11 — CLI

```python
# deploymint/cli.py
import click
import uvicorn
from rich.console import Console
from rich.table import Table

from deploymint import __version__
from deploymint.config import get_settings

console = Console()


@click.group()
@click.version_option(__version__)
def main():
    """DeployMint — local-first AI DevOps platform."""


@main.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
@click.option("--reload", is_flag=True)
def server(host, port, reload):
    """Start the DeployMint server."""
    s = get_settings()
    host, port = host or s.host, port or s.port
    if host not in ("127.0.0.1", "localhost"):
        console.print(
            f"[bold red]WARNING[/] binding to {host} exposes shell execution to your "
            "network. Only do this on a trusted network."
        )
    console.print(f"[bold green]DeployMint[/] → [cyan]http://{host}:{port}[/]")
    console.print(f"  home:  {s.home}")
    console.print(f"  model: {s.model}\n")
    uvicorn.run("deploymint.server:app", host=host, port=port, reload=reload)


@main.command()
def doctor():
    """Check that every prerequisite is available."""
    from deploymint.core.doctor import run_checks

    checks = run_checks()
    table = Table(title="DeployMint Doctor", show_lines=False)
    table.add_column("", width=2)
    table.add_column("Check", style="bold")
    table.add_column("Detail")

    icon = {"pass": "[green]✓[/]", "warn": "[yellow]![/]", "fail": "[red]✗[/]"}
    for c in checks:
        table.add_row(icon[c["status"]], c["name"], c["detail"])

    console.print(table)
    fails = [c for c in checks if c["status"] == "fail"]
    if fails:
        console.print("\n[bold red]Fix these:[/]")
        for c in fails:
            console.print(f"  • {c['name']}: [cyan]{c['fix']}[/]")
        raise SystemExit(1)
    console.print("\n[bold green]All required checks passed.[/]")
```

---

## Step 1.12 — Phase 1 acceptance test

Terminal A:

```bash
source venv/bin/activate && deploymint doctor
```

```bash
deploymint server
```

Terminal B:

```bash
curl -s localhost:8000/health
```

```bash
curl -s -X POST localhost:8000/api/projects -H 'content-type: application/json' -d '{"name":"sample-api","repo_path":"./tests/fixtures/sample_fastapi"}'
```

```bash
curl -s -X POST localhost:8000/api/projects/1/analyze | python -m json.tool
```

**Pass criteria — all of these:**

- `doctor` exits 0
- `POST /api/projects` returns 201 with a resolved absolute `repo_path`
- `analyze` returns `language: "python"`, `framework: "fastapi"`,
  `entrypoint: "main.py"`, `exposed_port: 8000`
- `dependency_graph.nodes` has ≥ 4 entries and `links` has ≥ 3
- `critical_files[0]` is `app/db.py` (the most-imported module)
- Registering `/` returns HTTP 400
- `pytest -m "not slow"` is green

Tick **Phase 1** in `README.md`. Next: `06-phase-2-generation.md`.

---

## Time budget

| Task | Hours |
|---|---|
| Package skeleton, pyproject, install | 1.0 |
| Config + sandbox + tests | 1.5 |
| DB models + database.py | 1.5 |
| Event bus | 1.0 |
| State schema | 0.5 |
| repo_scanner (walk, detect) | 2.5 |
| tree-sitter imports + graph | 3.0 |
| Fixtures (4 sample repos) | 1.5 |
| API + server + CLI + doctor | 3.0 |
| Tests + debugging | 2.5 |
| **Total** | **~18 h (2 days)** |

If you run over: cut fixtures to FastAPI only, and make `extract_imports` handle Python
only. JS/Go/Java import parsing is a Phase-2 nice-to-have.
