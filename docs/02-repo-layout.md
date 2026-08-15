# 02 — Repository Layout

Every file, with its single responsibility. If a file grows a second responsibility,
split it. `[P1]`–`[P7]` marks the phase that creates it.

```
DeployMint/
├── docs/                             # ← these planning docs (not shipped in the image)
├── venv/                             # Python 3.11, local dev only (gitignored)
├── Dockerfile                   [P1] # builds the ONE image the end user runs
├── docker-compose.yml            [P1] # app + postgres, the entire end-user surface
├── .env.example                  [P1] # ANTHROPIC_API_KEY, DEPLOYMINT_PROJECTS_DIR, ports
├── pyproject.toml                [P1] # app package metadata + deps — installed INTO the image
├── README.md                     [P7] # the public README — install, quickstart, GIF
├── LICENSE                       [P7] # Apache 2.0 (see 11-phase-7 §11.6 for why)
├── .gitignore                    [P0]
├── requirements.txt              [P0] # direct runtime deps — what the Dockerfile pip-installs
├── requirements-dev.txt          [P0] # + pytest, ruff, mypy, checkov — local dev only
├── requirements.lock.txt         [P0] # pip freeze, exact pins, reproducibility
├── Makefile                      [P1] # dev shortcuts: make build / up / logs / test
│
├── deploymint/                       # ── THE APP PACKAGE (built into the image) ──
│   ├── __init__.py               [P1] # __version__ only. Keep it empty otherwise.
│   ├── cli.py                    [P1] # thin CLI client — see below, this is NOT the app
│   ├── config.py                 [P1] # Settings via pydantic-settings; reads env vars only
│   ├── server.py                 [P1] # FastAPI app factory, router mounting, lifespan
│   ├── exceptions.py             [P1] # DeployMintError hierarchy
│   │
│   ├── db/
│   │   ├── __init__.py           [P1]
│   │   ├── models.py             [P1] # SQLAlchemy 2.0 models: Project, Run, AuditLog, Event
│   │   ├── database.py           [P1] # engine, SessionLocal, get_db dep, init_db
│   │   └── crud.py               [P2] # query helpers — keep SQL out of route handlers
│   │
│   ├── schemas/                      # Pydantic — the API contract
│   │   ├── __init__.py           [P1]
│   │   ├── project.py            [P1] # ProjectCreate, ProjectRead, ProjectList
│   │   ├── run.py                [P1] # RunCreate, RunRead, RunSummary
│   │   ├── artifacts.py          [P2] # GeneratedArtifacts — what the LLM must produce
│   │   └── chat.py               [P5] # ChatRequest, ChatResponse, Intent
│   │
│   ├── core/                         # infrastructure adapters — no business logic
│   │   ├── __init__.py           [P1]
│   │   ├── llm.py                [P2] # Claude client wrapper: complete(), complete_json(), health()
│   │   ├── prompts.py            [P2] # every prompt template, versioned. ONE file.
│   │   ├── repo_scanner.py       [P1] # tree-sitter parsing, import extraction, file walk
│   │   ├── graph_builder.py      [P1] # networkx DiGraph, PageRank, cycle detection
│   │   ├── docker_engine.py      [P4] # Docker SDK against the mounted host socket
│   │   ├── kube_engine.py        [P4] # kubectl subprocess against the mounted kubeconfig
│   │   ├── tmux_recorder.py      [P4] # libtmux session, pane capture, replay file
│   │   ├── events.py             [P1] # EventBus: per-run asyncio.Queue + DB persistence
│   │   └── sandbox.py            [P1] # safe_join, validate_repo_path — scoped to /workspace
│   │
│   ├── agents/                       # ── THE SWARM ──
│   │   ├── __init__.py           [P1]
│   │   ├── state.py              [P1] # DeployState TypedDict. FROZEN. See 01 §1.5
│   │   ├── base.py               [P1] # BaseAgent: name, emit(), run(state) contract
│   │   ├── architect.py          [P1] # language/framework detect + dependency graph + LLM summary
│   │   ├── smith.py              [P2] # Claude-backed artifact generation + validation
│   │   ├── templates.py          [P2] # deterministic Dockerfile/K8s fallbacks per stack
│   │   ├── warden.py             [P3] # Checkov + OPA orchestration + LLM explanations
│   │   ├── redteam.py            [P3] # adversarial probes (LLM, always on) + fixed checklist
│   │   ├── execution.py          [P4] # tmux + docker (via socket) + kubectl, the deploy sequence
│   │   ├── oracle.py             [P6] # IsolationForest + LLM-generated anomaly explanation
│   │   ├── remediator.py         [P6] # kubectl rollout undo on anomaly
│   │   ├── finops.py             [P6] # cost estimate / AWS CE, NL cost Q&A
│   │   └── graph.py              [P5] # LangGraph StateGraph assembly. Glue only.
│   │
│   ├── api/
│   │   ├── __init__.py           [P1]
│   │   ├── health.py             [P1] # GET /health, GET /api/doctor
│   │   ├── projects.py           [P1] # CRUD + analyze
│   │   ├── runs.py               [P1] # trigger, get, list, artifacts, cancel
│   │   ├── ws.py                 [P5] # WebSocket /ws/runs/{run_id}
│   │   ├── chat.py               [P5] # POST /api/chat — natural language router
│   │   └── costs.py              [P6] # GET /api/costs, POST /api/costs/query
│   │
│   ├── runner/
│   │   ├── __init__.py           [P1]
│   │   └── manager.py            [P1] # RunManager: spawn, track, cancel, persist. Semaphore.
│   │
│   ├── policies/                     # shipped inside the image
│   │   ├── no_root_user.rego     [P3]
│   │   ├── no_sensitive_ports.rego [P3]
│   │   └── resource_limits.rego  [P3]
│   │
│   ├── data/
│   │   ├── fewshot.jsonl         [P2] # 15–25 curated (stack → artifacts) pairs
│   │   ├── rate_card.json        [P6] # cloud $/vCPU-hr, $/GB-hr for local estimation
│   │   └── sample_cost_export.json [P6] # AWS Cost Explorer shape, for the demo
│   │
│   └── web/                          # THIS is the primary user interface — see 01 §1.4 decision 13
│       ├── templates/
│       │   ├── base.html         [P6] # layout, HTMX + CSS include
│       │   ├── index.html        [P6] # project list + register form
│       │   ├── project.html      [P6] # graph, run history
│       │   ├── run.html          [P6] # live timeline, artifacts, security, terminal
│       │   └── partials/         [P6] # HTMX fragments: _run_row, _finding, _log_line
│       └── static/
│           ├── app.css           [P6]
│           ├── app.js            [P6] # WebSocket client, log appender, graph render
│           └── vendor/           [P6] # htmx.min.js, cytoscape.min.js (vendored, no CDN)
│
├── tests/
│   ├── conftest.py               [P1] # throwaway Postgres db, test client, sample repos
│   ├── fixtures/
│   │   ├── sample_fastapi/       [P1] # tiny real FastAPI app — the primary demo target
│   │   ├── sample_flask/         [P2]
│   │   ├── sample_express/       [P2]
│   │   └── sample_go/            [P2]
│   ├── test_config.py            [P1]
│   ├── test_sandbox.py           [P1] # path traversal attempts MUST be rejected
│   ├── test_architect.py         [P1]
│   ├── test_smith.py             [P2] # mocked LLM; asserts fallback fires on API failure
│   ├── test_warden.py            [P3] # known-bad Dockerfile MUST be blocked
│   ├── test_redteam.py           [P3]
│   ├── test_execution.py         [P4] # marked slow; needs the docker socket mount
│   ├── test_graph.py             [P5]
│   ├── test_api_projects.py      [P1]
│   ├── test_api_runs.py          [P1]
│   └── test_finops.py            [P6]
│
└── scripts/
    ├── demo.sh                   [P7] # the exact demo sequence, scripted
    ├── reset.sh                  [P1] # docker compose down -v && up -d — wipe and restart clean
    └── build_fewshot.py          [P2] # scrape/curate few-shot pairs into fewshot.jsonl
```

---

## 2.1 The two front doors — and neither of them is `pip`

**The web UI (`http://localhost:8000`) is the primary interface.** It is served by the
same FastAPI process, from the same running container — no separate build step, no
separate deploy.

**`deploymint` the CLI still exists, but it is a thin HTTP + WebSocket client**, not a
second copy of the app. It talks to whatever container is already running:

```python
# deploymint/cli.py — the entire mental model
@click.command()
@click.argument("path")
def up(path):
    """POST to the running container's API, then stream the WS to this terminal."""
    resp = httpx.post(f"{server_url}/api/projects", json={"repo_path": resolve(path)})
    run_id = httpx.post(f"{server_url}/api/projects/{resp['id']}/runs").json()["run_id"]
    stream_websocket(f"{server_url}/ws/runs/{run_id}")   # render with Rich
```

It needs `click`, `httpx`, `websockets`, `rich` — nothing else. It does **not** need
tree-sitter, checkov, langgraph, or the docker SDK, because it never runs any agent logic
itself; the container does. This is exactly the relationship `kubectl` has to
`kube-apiserver`, or the `docker` CLI has to `dockerd`.

Whether you ship this CLI as a tiny separate `pip install deploymint-cli` package, a
single-file script, or `docker compose exec app deploymint up ...` is a Phase 5 polish
decision (`09-phase-5-orchestration.md`) — it does not change anything about the app
itself, because it is a pure client of the API that's already fully specified in
`03-data-model.md`.

---

## 2.2 The layering rule (unchanged)

```
api/  ──────────►  runner/  ──────────►  agents/  ──────────►  core/
 │                    │                     │                    │
 └──► schemas/        └──► db/              └──► agents/state.py └──► (stdlib, SDKs)
```

**Dependencies point one direction only.**

- `core/` imports nothing from `agents/`, `api/`, or `runner/`. It is pure infrastructure:
  "how to talk to Docker over the mounted socket", "how to parse a file".
- `agents/` imports `core/` and `state.py`. It contains the *reasoning*.
- `api/` imports `runner/` and `schemas/`. Route handlers should be **under 20 lines**.
- `db/` is imported by `api/` and `runner/`. **Agents never touch the database.**
  Agents take state in, return state out. This is what makes them testable without a DB
  and reusable from the thin CLI's perspective — the CLI never imports `agents/` at all.

---

## 2.3 `Dockerfile` (complete, Phase 1)

```dockerfile
FROM python:3.11-slim AS base

# System deps baked in ONCE — see 00-prerequisites.md §0.3 for why each is here
RUN apt-get update && apt-get install -y --no-install-recommends \
        git tmux curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# kubectl — pinned version, not "latest"
ARG KUBECTL_VERSION=v1.31.0
RUN curl -Lo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl

# opa — pinned version
ARG OPA_VERSION=v1.1.0
RUN curl -Lo /usr/local/bin/opa \
        "https://openpolicyagent.org/downloads/${OPA_VERSION}/opa_linux_amd64_static" \
    && chmod +x /usr/local/bin/opa

# docker CLI only (client) — talks to the mounted host socket, no daemon needed here
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
        | tar xz -C /usr/local/bin --strip-components=1 docker/docker

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY deploymint/ ./deploymint/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "deploymint.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Every tool in `00-prerequisites.md` §0.3 has exactly one line here. This file *is* the
answer to "how does the end user get Checkov/OPA/kubectl/tree-sitter without installing
anything" — they get it because it's already inside the image they pulled.

---

## 2.4 `docker-compose.yml` (complete, Phase 1)

```yaml
services:
  app:
    build: .
    ports:
      - "127.0.0.1:${DEPLOYMINT_PORT:-8000}:8000"
    environment:
      DATABASE_URL: postgresql+psycopg://deploymint:deploymint@db:5432/deploymint
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      DEPLOYMINT_MODEL: ${DEPLOYMINT_MODEL:-claude-opus-5}
      DEPLOYMINT_KUBE_CONTEXT: ${KUBE_CONTEXT:-}
    volumes:
      - ${DEPLOYMINT_PROJECTS_DIR:-./projects}:/workspace
      - /var/run/docker.sock:/var/run/docker.sock
      - ${HOME}/.kube/config:/root/.kube/config:ro
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: deploymint
      POSTGRES_PASSWORD: deploymint
      POSTGRES_DB: deploymint
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U deploymint"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

volumes:
  pgdata:
```

**`depends_on: condition: service_healthy`** is the detail that prevents the classic
"app started before Postgres was ready" race — the app container waits for Postgres's
own healthcheck, not just for the process to exist.

**The kubeconfig mount is a bind mount to a specific file, not a directory**, and it's
`:ro`. If the file doesn't exist on the host, Compose will refuse to start with a clear
error — document the "no cluster, that's fine, comment out this line" escape hatch in
`.env.example` and the README.

---

## 2.5 `.env.example` (complete, Phase 1)

```bash
# Required — get one at https://console.anthropic.com
ANTHROPIC_API_KEY=

# Where your own projects live on this machine. Everything under this directory
# becomes visible inside the app at /workspace/<name>.
DEPLOYMINT_PROJECTS_DIR=./projects

# Port the dashboard is served on
DEPLOYMINT_PORT=8000

# Optional — override the default model
DEPLOYMINT_MODEL=claude-opus-5

# Optional — which kubectl context to deploy into. Leave blank to use whatever
# is current in the mounted ~/.kube/config, or to fall back to `docker run` if
# no cluster is reachable at all.
KUBE_CONTEXT=
```

---

## 2.6 `pyproject.toml`

Still exists — it's how `pip install -e .` works **inside the Dockerfile build**, and how
local dev installs the same package. It is **not** how the end user gets the app; they
never run this command themselves.

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "deploymint"
version = "0.1.0"
description = "AI agent swarm for secure deployment: reads your code, writes your configs, proves they're safe, deploys them. Runs as a self-hosted Docker Compose app."
readme = "README.md"
requires-python = ">=3.11,<3.14"
license = { text = "Apache-2.0" }
authors = [{ name = "Haider Bukhari", email = "haider@ottooptics.io" }]
keywords = ["devops", "kubernetes", "docker", "ai-agents", "security", "finops"]

dependencies = [
  "fastapi>=0.111",
  "uvicorn[standard]>=0.30",
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.1",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "jinja2>=3.1",
  "python-multipart>=0.0.9",
  "websockets>=12.0",
  "httpx>=0.27",
  "click>=8.1",
  "rich>=13.7",
  "pyyaml>=6.0",
  "tree-sitter>=0.22",
  "tree-sitter-language-pack>=0.2",
  "networkx>=3.3",
  "docker>=7.0",
  "libtmux>=0.37",
  "langgraph>=0.2",
  "langchain-core>=0.3",
  "anthropic>=0.69",
  "scikit-learn>=1.5",
]

[project.optional-dependencies]
aws = ["boto3>=1.34"]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.5", "mypy>=1.11", "checkov>=3.2.0"]

[project.scripts]
deploymint = "deploymint.cli:main"

[tool.setuptools.packages.find]
include = ["deploymint*"]

[tool.setuptools.package-data]
deploymint = [
  "policies/*.rego",
  "data/*.json",
  "data/*.jsonl",
  "web/templates/*.html",
  "web/templates/partials/*.html",
  "web/static/*",
  "web/static/vendor/*",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "S"]
ignore = ["S101"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "slow: requires the mounted docker socket",
]
```

`checkov` moved into the `dev` extra rather than being fully external — it's baked
straight into the Dockerfile via `requirements.txt` for the running app (§2.3), and dev
installs pull it the same way local Checkov-scanning tests need it. There's no more
"don't install it, the user will" story, because there is no end-user pip install at all.

---

## 2.7 `Makefile`

```makefile
.PHONY: build up down logs test lint fmt clean reset shell

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f app

shell:
	docker compose exec app bash

test:
	venv/bin/pytest -v -m "not slow"

test-all:
	venv/bin/pytest -v

lint:
	venv/bin/ruff check deploymint tests

fmt:
	venv/bin/ruff format deploymint tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info

reset:
	docker compose down -v
	docker compose up -d
```

`reset` now tears down and recreates the whole compose stack, including the Postgres
volume — this is the one-command "start over completely clean" path, replacing the
original `kind delete/create cluster` reset (which is still useful for the *dev* cluster
used to test the deploy path — see `00-prerequisites.md` §0.2 — but is no longer the
thing an end user ever touches).

---

## 2.8 Naming conventions (unchanged)

| Thing | Convention | Example |
|---|---|---|
| Agent class | `<Name>Agent` | `ArchitectAgent`, `SecurityWardenAgent` |
| Agent module | lowercase, no suffix | `architect.py`, `warden.py` |
| LangGraph node fn | `<name>_node` | `architect_node(state)` |
| Pydantic API model | `<Entity><Verb>` | `ProjectCreate`, `RunRead` |
| SQLAlchemy model | singular PascalCase | `Project`, `Run`, `AuditLog` |
| Event type | `<agent>.<phase>` | `architect.start`, `execution.log`, `warden.done` |
| Run ID | `run_` + 12 hex chars | `run_a3f8c21b9de0` |
| Image tag (of the user's built app) | `deploymint/<project>:<run_id>` | `deploymint/myapi:run_a3f8c21b9de0` |
| Rego package | `deploymint.<rule>` | `package deploymint.no_root_user` |
| Finding ID | `DM_<UPPER_SNAKE>` (ours), `CKV_*` (checkov) | `DM_ROOT_USER` |

Next: `03-data-model.md`.
