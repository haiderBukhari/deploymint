# 03 — Data Model & API Surface

## 3.1 Database schema

Four tables, **Postgres 16**, running as the bundled `db` service from
`02-repo-layout.md` §2.4. Created by `Base.metadata.create_all()` on app startup — no
Alembic yet (pre-1.0, schema still moves; see `01-architecture.md` §1.4 decision 5).

The app never manages Postgres itself — Compose owns the container, the volume, and the
healthcheck. The app just connects to `db:5432` using the `DATABASE_URL` Compose already
injected as an environment variable (`02-repo-layout.md` §2.4). There is nothing here for
an end user to configure.

### `projects`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL UNIQUE | slug-safe; used in image tags |
| `repo_path` | TEXT NOT NULL | **always under `/workspace`** — validated by sandbox |
| `language` | TEXT | filled by first analyze |
| `framework` | TEXT | |
| `entrypoint` | TEXT | |
| `exposed_port` | INTEGER | default 8000 |
| `analysis` | JSONB | full `RepoAnalysis` from the last analyze |
| `created_at` | TIMESTAMPTZ | |
| `last_analyzed_at` | TIMESTAMPTZ NULL | |

### `runs`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `run_a3f8c21b9de0` |
| `project_id` | INTEGER FK → projects.id | |
| `status` | TEXT | `pending`→`running`→`success` \| `failed` \| `blocked` \| `cancelled` |
| `current_node` | TEXT | which agent is active right now (drives the UI spinner) |
| `trigger` | TEXT | `ui` \| `cli` \| `chat` \| `api` |
| `force` | BOOLEAN | security gate bypassed |
| `analysis` | JSONB | snapshot at run time |
| `artifacts` | JSONB | `Artifacts` TypedDict |
| `security` | JSONB | `SecurityReport` |
| `deployment` | JSONB | `Deployment` |
| `cost` | JSONB | `CostReport` |
| `errors` | JSONB | list[str] |
| `model_used` | TEXT | e.g. `claude-opus-5` |
| `input_tokens` / `output_tokens` | INTEGER NULL | for the per-run LLM cost readout in the UI |
| `duration_ms` | INTEGER NULL | |
| `created_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ NULL | |

Index: `(project_id, created_at DESC)` — the run-history query.

`JSONB`, not plain `JSON` — Postgres's binary JSON type. It costs nothing extra to use
and means a query like *"every run blocked by `DM_ROOT_USER`"* can be a real indexed
query later (`security @> '{"passed": false}'` with a GIN index) instead of a full scan,
without changing a single line of Python. Same `Mapped[dict]` column type either way.

### `events` — the live timeline, and the replay source

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `run_id` | TEXT FK → runs.id | |
| `seq` | INTEGER | monotonic per run; the WS client resumes from `?since=seq` |
| `type` | TEXT | `architect.start`, `execution.log`, `warden.finding`, … |
| `payload` | JSONB | shape depends on type |
| `ts` | TIMESTAMPTZ | |

Index: `UNIQUE (run_id, seq)` — makes the sequence contract enforceable, not
aspirational; a double-emit bug becomes an immediate constraint violation instead of a
duplicated line in someone's terminal.

**Why persist events:** a browser refresh mid-run must not lose the timeline. The WS
handler replays rows `seq > since` from this table, then attaches to the live in-memory
queue described in `01-architecture.md` §1.6.

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
| `ts` | TIMESTAMPTZ | |

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

This is **not** cryptographic signing (no key, no external anchor) — someone with write
access to Postgres could recompute the whole chain. Say so honestly. What it *does* give
you: tamper **evidence** against accidental edits and partial corruption, and a
verifiable ordering. That is a real, defensible claim.

---

## 3.2 SQLAlchemy models (complete, Phase 1)

```python
# deploymint/db/models.py
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, DateTime, Text, Boolean, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    analysis: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list["Run"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    current_node: Mapped[str | None] = mapped_column(String(50))
    trigger: Mapped[str] = mapped_column(String(20), default="api")
    force: Mapped[bool] = mapped_column(Boolean, default=False)

    analysis: Mapped[dict | None] = mapped_column(JSONB)
    artifacts: Mapped[dict | None] = mapped_column(JSONB)
    security: Mapped[dict | None] = mapped_column(JSONB)
    deployment: Mapped[dict | None] = mapped_column(JSONB)
    cost: Mapped[dict | None] = mapped_column(JSONB)
    errors: Mapped[list | None] = mapped_column(JSONB, default=list)

    model_used: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_runs_project_created", "project_id", "created_at"),)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_events_run_seq", "run_id", "seq", unique=True),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    command: Mapped[str] = mapped_column(Text)
    output: Mapped[str] = mapped_column(Text, default="")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_audit_run_seq", "run_id", "seq", unique=True),)
```

### `database.py` — connecting to the compose `db` service

```python
# deploymint/db/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from deploymint.config import get_settings
from deploymint.db.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        # DATABASE_URL is injected by docker-compose.yml — see 02-repo-layout.md §2.4.
        # No pragmas, no WAL mode: this is real Postgres, running as its own service,
        # with its own connection pooling and its own concurrency story.
        _engine = create_engine(s.database_url, pool_pre_ping=True, echo=s.sql_echo)
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

**`pool_pre_ping=True` replaces the old WAL/`busy_timeout` pragmas entirely.** Those
existed to work around SQLite being a single file with no real concurrency control.
Postgres already handles concurrent readers and writers correctly; the only thing worth
guarding against is a stale pooled connection if the `db` container ever restarts —
`pool_pre_ping` checks the connection is alive before handing it out, and transparently
reconnects if not.

**Lazy globals still matter, for a different reason now.** `get_settings()` reads
`DATABASE_URL` from the environment at call time. Tests set this to a throwaway Postgres
database (see `12-testing-strategy.md`) rather than a tmp SQLite file, but the pattern —
never build the engine at import time — is unchanged and for the same reason: so tests
don't accidentally point at the real `db` service.

---

## 3.3 Pydantic API schemas (Phase 1)

```python
# deploymint/schemas/project.py
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_path: str    # must resolve under /workspace — see core/sandbox.py

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
from pydantic import BaseModel, computed_field

RunStatus = Literal["pending", "running", "success", "failed", "blocked", "cancelled"]

# Claude Opus 5 pricing — $5/1M input, $25/1M output. Update if the model changes.
_INPUT_PER_TOKEN = 5e-6
_OUTPUT_PER_TOKEN = 25e-6


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
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def llm_cost_usd(self) -> float | None:
        """Surfacing the real per-run inference cost in the UI is a small, honest
        demonstration of the FinOps thesis on the product itself. Ship it."""
        if self.input_tokens is None:
            return None
        return round(
            self.input_tokens * _INPUT_PER_TOKEN
            + (self.output_tokens or 0) * _OUTPUT_PER_TOKEN, 4,
        )
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
Even a strong hosted model will occasionally hand you a Dockerfile wrapped in
` ```dockerfile ` fences, or a manifest missing `kind`. This catches it and falls back to
a template. **Nothing unvalidated reaches the disk.** See `06-phase-2-generation.md` for
how the Smith uses this.

---

## 3.4 Full REST API surface

### Health & diagnostics

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | `{"status":"ok","version":"0.1.0"}` |
| GET | `/api/doctor` | LLM reachability, DB connectivity, mounted-socket/kubeconfig status |

### Projects

| Method | Path | Body / Params | Returns |
|---|---|---|---|
| POST | `/api/projects` | `ProjectCreate` | `ProjectRead` (201) |
| GET | `/api/projects` | — | `list[ProjectRead]` |
| GET | `/api/projects/{id}` | — | `ProjectRead` |
| DELETE | `/api/projects/{id}` | — | 204 (cascades runs) |
| POST | `/api/projects/{id}/analyze` | — | `RepoAnalysis` — Architect only, fast |
| GET | `/api/projects/{id}/graph` | — | `{nodes, links}` for the visualizer |

`analyze` is separate from `run` on purpose. It's sub-second and gives the user immediate
feedback that DeployMint *understood their code* before they commit to a deploy — the
best first-impression moment in the product. Surface it prominently in the web UI.

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

### Chat / NL router (Phase 5)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/chat` | `{"message": "...", "project_id": null, "session_id": null}` | `{"intent","reply","action_taken","run_id"}` |

### Costs (Phase 6)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/costs` | `CostReport` across all projects |
| GET | `/api/costs/{project_id}` | per-project breakdown |
| POST | `/api/costs/query` | `{"question": "which service costs the most?"}` → `{"answer","data"}` |

### Web UI (Phase 6) — the primary interface

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
| `architect.done` | `{language, framework, file_count, entrypoint, architecture_summary}` | Architect |
| `smith.thinking` | `{model}` | Smith |
| `smith.done` | `{generated_by, files: [names]}` | Smith |
| `warden.finding` | `Finding` (now may include an LLM `explanation`) | Warden (one per finding) |
| `warden.done` | `{passed, critical, high, medium, low}` | Warden |
| `redteam.probe` | `{probe_name, result}` | Red Team |
| `redteam.done` | `{findings_count}` | Red Team |
| `execution.log` | `{line, stream: "stdout"\|"stderr"}` | Execution (high volume) |
| `execution.stage` | `{stage: "build"\|"apply"\|"rollout"}` | Execution |
| `execution.done` | `{image_tag, pod_name, status}` | Execution |
| `oracle.metric` | `{cpu, memory, ts}` | Oracle |
| `oracle.anomaly` | `{score, reason, explanation}` | Oracle |
| `finops.done` | `CostReport` | FinOps |
| `error` | `{node, message}` | any |

`execution.log` is the only high-frequency event. **Batch it**: buffer lines for 100 ms
and send arrays, or a `docker build` will produce hundreds of frames per second and
saturate the WebSocket.

Next: `04-agents-spec.md`.
