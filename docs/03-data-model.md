# 03 — Data Model & API Surface

## 3.1 Database schema

Four tables. SQLite. Created by `Base.metadata.create_all()` on server start.

### `projects`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL UNIQUE | slug-safe; used in image tags |
| `repo_path` | TEXT NOT NULL | **resolved absolute path** — validated by sandbox |
| `language` | TEXT | filled by first analyze |
| `framework` | TEXT | |
| `entrypoint` | TEXT | |
| `exposed_port` | INTEGER | default 8000 |
| `analysis` | JSON | full `RepoAnalysis` from the last analyze |
| `created_at` | DATETIME | |
| `last_analyzed_at` | DATETIME NULL | |

### `runs`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `run_a3f8c21b9de0` |
| `project_id` | INTEGER FK → projects.id | |
| `status` | TEXT | `pending`→`running`→`success` \| `failed` \| `blocked` \| `cancelled` |
| `current_node` | TEXT | which agent is active right now (drives the UI spinner) |
| `trigger` | TEXT | `ui` \| `cli` \| `chat` \| `api` |
| `force` | BOOLEAN | security gate bypassed |
| `analysis` | JSON | snapshot at run time |
| `artifacts` | JSON | `Artifacts` TypedDict |
| `security` | JSON | `SecurityReport` |
| `deployment` | JSON | `Deployment` |
| `cost` | JSON | `CostReport` |
| `errors` | JSON | list[str] |
| `model_used` | TEXT | e.g. `ollama/llama3.1:8b` |
| `duration_ms` | INTEGER NULL | |
| `created_at` | DATETIME | |
| `completed_at` | DATETIME NULL | |

Index: `(project_id, created_at DESC)` — the run-history query.

### `events` — the live timeline, and the replay source

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `run_id` | TEXT FK → runs.id | |
| `seq` | INTEGER | monotonic per run; the WS client resumes from `?since=seq` |
| `type` | TEXT | `architect.start`, `execution.log`, `warden.finding`, … |
| `payload` | JSON | shape depends on type |
| `ts` | DATETIME | |

Index: `(run_id, seq)`.

**Why persist events:** a browser refresh mid-run must not lose the timeline. The WS
handler replays rows `seq > since` from this table, then attaches to the live queue.

### `audit_logs` — the tamper-evident chain

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `run_id` | TEXT FK | |
| `seq` | INTEGER | per-run |
| `agent` | TEXT | who acted |
| `action` | TEXT | `shell_exec`, `file_write`, `llm_call`, `k8s_apply` |
| `command` | TEXT | exact argv, joined for display |
| `output` | TEXT | captured stdout+stderr (truncated at 64 KB) |
| `exit_code` | INTEGER NULL | |
| `prev_hash` | TEXT | hash of the previous row in this run |
| `hash` | TEXT | `sha256(prev_hash + run_id + seq + agent + action + command + output)` |
| `ts` | DATETIME | |

### The hash chain — implement it, it takes 20 minutes

```python
import hashlib, json

GENESIS = "0" * 64

def compute_hash(prev_hash: str, row: dict) -> str:
    payload = json.dumps({
        "prev": prev_hash,
        "run_id": row["run_id"],
        "seq": row["seq"],
        "agent": row["agent"],
        "action": row["action"],
        "command": row["command"],
        "output": row["output"],
        "exit_code": row.get("exit_code"),
        "ts": row["ts"].isoformat(),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
```

Verification endpoint `GET /api/runs/{id}/audit/verify` recomputes the chain and returns
`{"valid": bool, "broken_at_seq": int | null}`.

This is **not** cryptographic signing (no key, no external anchor) — an attacker with
write access to the DB could recompute the whole chain. Say so honestly. What it *does*
give you: tamper **evidence** against accidental edits and partial corruption, and a
verifiable ordering. That is a real, defensible claim. Claiming more would be
overselling, and a technical reviewer will catch it immediately.

---

## 3.2 SQLAlchemy models (complete, Phase 1)

```python
# deploymint/db/models.py
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, DateTime, Text, Boolean, JSON, ForeignKey, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    repo_path: Mapped[str] = mapped_column(String(1024))
    language: Mapped[str | None] = mapped_column(String(50))
    framework: Mapped[str | None] = mapped_column(String(50))
    entrypoint: Mapped[str | None] = mapped_column(String(255))
    exposed_port: Mapped[int] = mapped_column(Integer, default=8000)
    analysis: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime)

    runs: Mapped[list["Run"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    current_node: Mapped[str | None] = mapped_column(String(50))
    trigger: Mapped[str] = mapped_column(String(20), default="api")
    force: Mapped[bool] = mapped_column(Boolean, default=False)

    analysis: Mapped[dict | None] = mapped_column(JSON)
    artifacts: Mapped[dict | None] = mapped_column(JSON)
    security: Mapped[dict | None] = mapped_column(JSON)
    deployment: Mapped[dict | None] = mapped_column(JSON)
    cost: Mapped[dict | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON, default=list)

    model_used: Mapped[str | None] = mapped_column(String(100))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    project: Mapped[Project] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_runs_project_created", "project_id", "created_at"),)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_events_run_seq", "run_id", "seq"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    command: Mapped[str] = mapped_column(Text)
    output: Mapped[str] = mapped_column(Text, default="")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_audit_run_seq", "run_id", "seq"),)
```

### `database.py` — note the WAL pragma

```python
# deploymint/db/database.py
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from deploymint.config import get_settings
from deploymint.db.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_engine(
            s.database_url,
            connect_args={"check_same_thread": False},
            echo=s.sql_echo,
        )

        @event.listens_for(_engine, "connect")
        def _set_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")     # concurrent read + write
            cur.execute("PRAGMA synchronous=NORMAL")   # fast enough, still durable
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")    # wait, don't error, on lock
            cur.close()

    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())


def get_db():
    """FastAPI dependency."""
    db: Session = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
```

**`busy_timeout=5000` matters.** Without it, a WebSocket read racing an agent write
raises `database is locked` and the run appears to crash. This one line prevents a
confusing multi-hour debugging session in Phase 5.

**Lazy globals matter too.** `get_settings()` reads `DEPLOYMINT_HOME` at call time, so
tests can point it at a tmpdir. If you build the engine at import time, every test
shares your real database.

---

## 3.3 Pydantic API schemas (Phase 1)

```python
# deploymint/schemas/project.py
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_path: str

    @field_validator("name")
    @classmethod
    def slug_safe(cls, v: str) -> str:
        cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in v.lower())
        if not cleaned.strip("-"):
            raise ValueError("name must contain at least one alphanumeric character")
        return cleaned


class ProjectRead(BaseModel):
    id: int
    name: str
    repo_path: str
    language: str | None = None
    framework: str | None = None
    entrypoint: str | None = None
    exposed_port: int = 8000
    created_at: datetime
    last_analyzed_at: datetime | None = None
    run_count: int = 0

    model_config = {"from_attributes": True}
```

```python
# deploymint/schemas/run.py
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel

RunStatus = Literal["pending", "running", "success", "failed", "blocked", "cancelled"]


class RunCreate(BaseModel):
    force: bool = False
    trigger: str = "api"
    skip_deploy: bool = False      # generate + scan only; used heavily during dev


class RunRead(BaseModel):
    id: str
    project_id: int
    status: RunStatus
    current_node: str | None = None
    analysis: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    security: dict[str, Any] | None = None
    deployment: dict[str, Any] | None = None
    cost: dict[str, Any] | None = None
    errors: list[str] = []
    model_used: str | None = None
    duration_ms: int | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}
```

```python
# deploymint/schemas/artifacts.py — the LLM's output contract
import yaml
from pydantic import BaseModel, Field, field_validator


class GeneratedArtifacts(BaseModel):
    """What the Artifact Smith MUST produce. Enforced before anything is written."""

    dockerfile: str = Field(min_length=20)
    dockerignore: str = ""
    k8s_deployment: str = Field(min_length=20)
    k8s_service: str = Field(min_length=10)
    reasoning: str = ""

    @field_validator("dockerfile")
    @classmethod
    def must_look_like_a_dockerfile(cls, v: str) -> str:
        upper = v.upper()
        if "FROM " not in upper:
            raise ValueError("Dockerfile has no FROM instruction")
        if "```" in v:
            raise ValueError("Dockerfile still contains markdown fences")
        return v.strip() + "\n"

    @field_validator("k8s_deployment", "k8s_service")
    @classmethod
    def must_be_valid_yaml(cls, v: str) -> str:
        try:
            doc = yaml.safe_load(v)
        except yaml.YAMLError as e:
            raise ValueError(f"invalid YAML: {e}") from e
        if not isinstance(doc, dict) or "kind" not in doc:
            raise ValueError("YAML is not a Kubernetes resource (no 'kind')")
        return v.strip() + "\n"
```

This class is the difference between a demo that works and one that explodes on stage.
An 8B local model *will* hand you a Dockerfile wrapped in ` ```dockerfile ` fences, or a
manifest missing `kind`. This catches it, triggers one repair attempt, then falls back
to a template. **Nothing unvalidated reaches the disk.**

---

## 3.4 Full REST API surface

### Health & diagnostics

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | `{"status":"ok","version":"0.1.0"}` |
| GET | `/api/doctor` | JSON version of `deploymint doctor` — every prerequisite check |

### Projects

| Method | Path | Body / Params | Returns |
|---|---|---|---|
| POST | `/api/projects` | `ProjectCreate` | `ProjectRead` (201) |
| GET | `/api/projects` | — | `list[ProjectRead]` |
| GET | `/api/projects/{id}` | — | `ProjectRead` |
| DELETE | `/api/projects/{id}` | — | 204 (cascades runs) |
| POST | `/api/projects/{id}/analyze` | — | `RepoAnalysis` — Architect only, fast, no LLM |
| GET | `/api/projects/{id}/graph` | — | `{nodes, links}` for the visualizer |

`analyze` is separate from `run` on purpose. It is sub-second, needs no LLM, and gives
the user immediate feedback that DeployMint *understood their code* before they commit
to a deploy. It is the best first-impression moment in the product — surface it prominently.

### Runs

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/projects/{id}/runs` | `RunCreate` | `{"run_id": "...", "status":"pending"}` (202) |
| GET | `/api/runs` | `?project_id=&status=&limit=` | `list[RunRead]` |
| GET | `/api/runs/{run_id}` | — | `RunRead` |
| POST | `/api/runs/{run_id}/cancel` | — | `{"cancelled": true}` |
| GET | `/api/runs/{run_id}/events` | `?since=0` | `list[Event]` |
| GET | `/api/runs/{run_id}/artifacts` | — | `{filename: content}` |
| GET | `/api/runs/{run_id}/artifacts/{name}` | — | `text/plain` raw file |
| GET | `/api/runs/{run_id}/audit` | — | `list[AuditLog]` |
| GET | `/api/runs/{run_id}/audit/verify` | — | `{"valid":bool,"broken_at_seq":int\|null}` |
| GET | `/api/runs/{run_id}/session` | — | raw tmux recording, `text/plain` |

### WebSocket

| Path | Protocol |
|---|---|
| `/ws/runs/{run_id}` | Client may send `{"since": <seq>}` on connect. Server replays persisted events with `seq > since`, then streams live frames. Server closes on terminal status. |

Frame shape:

```json
{ "seq": 42, "type": "execution.log", "ts": "2026-08-12T10:15:03Z",
  "payload": { "line": "Step 3/7 : RUN pip install -r requirements.txt" } }
```

### Chat / tmux.ai (Phase 5)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/chat` | `{"message": "...", "project_id": null, "session_id": null}` | `{"intent","reply","action_taken","run_id"}` |

### Costs (Phase 6)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/costs` | `CostReport` across all projects |
| GET | `/api/costs/{project_id}` | per-project breakdown |
| POST | `/api/costs/query` | `{"question": "which service costs the most?"}` → `{"answer","data"}` |

### Web UI (Phase 6)

| Path | Renders |
|---|---|
| `GET /` | `index.html` — project list + register form |
| `GET /projects/{id}` | `project.html` — graph, run history |
| `GET /runs/{run_id}` | `run.html` — live timeline |

---

## 3.5 Event type catalogue

Fix these strings now; the UI switches on them.

| Type | Payload | Emitted by |
|---|---|---|
| `run.start` | `{project_name, repo_path}` | RunManager |
| `run.end` | `{status, duration_ms}` | RunManager |
| `node.enter` | `{node}` | graph wrapper |
| `node.exit` | `{node, ms}` | graph wrapper |
| `architect.done` | `{language, framework, file_count, entrypoint}` | Architect |
| `smith.thinking` | `{model}` | Smith |
| `smith.done` | `{generated_by, files: [names]}` | Smith |
| `warden.finding` | `Finding` | Warden (one per finding) |
| `warden.done` | `{passed, critical, high, medium, low}` | Warden |
| `redteam.probe` | `{probe_name, result}` | Red Team |
| `redteam.done` | `{findings_count}` | Red Team |
| `execution.log` | `{line, stream: "stdout"\|"stderr"}` | Execution (high volume) |
| `execution.stage` | `{stage: "build"\|"load"\|"apply"\|"rollout"}` | Execution |
| `execution.done` | `{image_tag, pod_name, status}` | Execution |
| `oracle.metric` | `{cpu, memory, ts}` | Oracle |
| `oracle.anomaly` | `{score, reason}` | Oracle |
| `finops.done` | `CostReport` | FinOps |
| `error` | `{node, message}` | any |

`execution.log` is the only high-frequency event. **Batch it**: buffer lines for 100 ms
and send arrays, or a `docker build` will produce hundreds of frames per second and
saturate the WebSocket.

Next: `04-agents-spec.md`.
