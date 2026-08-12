# 02 — Repository Layout

Every file, with its single responsibility. If a file grows a second responsibility,
split it. `[P1]`–`[P7]` marks the phase that creates it.

```
DeployMint/
├── docs/                             # ← these planning docs (not shipped in the wheel)
├── venv/                             # Python 3.11 (gitignored)
├── pyproject.toml               [P1] # package metadata, deps, entry point, tool config
├── README.md                    [P7] # the public README — install, quickstart, GIF
├── LICENSE                      [P7] # Apache 2.0 (see 11-phase-7 §11.6 for why)
├── .gitignore                   [P0]
├── requirements.txt             [P0] # direct runtime deps (mirrors pyproject)
├── requirements-dev.txt         [P0] # + pytest, ruff, mypy, build, checkov
├── requirements.lock.txt        [P0] # pip freeze, exact pins, reproducibility
├── Makefile                     [P1] # dev shortcuts: make run / test / lint / clean
│
├── deploymint/                       # ── THE PACKAGE ──
│   ├── __init__.py              [P1] # __version__ only. Keep it empty otherwise.
│   ├── cli.py                   [P1] # Click group: server, doctor, up, init, ui, export
│   ├── config.py                [P1] # Settings via pydantic-settings; lazy DEPLOYMINT_HOME
│   ├── server.py                [P1] # FastAPI app factory, router mounting, lifespan
│   ├── exceptions.py            [P1] # DeployMintError hierarchy
│   │
│   ├── db/
│   │   ├── __init__.py          [P1]
│   │   ├── models.py            [P1] # SQLAlchemy 2.0 models: Project, Run, AuditLog, Event
│   │   ├── database.py          [P1] # engine, SessionLocal, get_db dep, init_db, WAL pragma
│   │   └── crud.py              [P2] # query helpers — keep SQL out of route handlers
│   │
│   ├── schemas/                      # Pydantic — the API contract
│   │   ├── __init__.py          [P1]
│   │   ├── project.py           [P1] # ProjectCreate, ProjectRead, ProjectList
│   │   ├── run.py               [P1] # RunCreate, RunRead, RunSummary
│   │   ├── artifacts.py         [P2] # GeneratedArtifacts — what the LLM must produce
│   │   └── chat.py              [P5] # ChatRequest, ChatResponse, Intent
│   │
│   ├── core/                         # infrastructure adapters — no business logic
│   │   ├── __init__.py          [P1]
│   │   ├── llm.py               [P2] # get_llm(), complete(), complete_json(), health()
│   │   ├── prompts.py           [P2] # every prompt template, versioned. ONE file.
│   │   ├── repo_scanner.py      [P1] # tree-sitter parsing, import extraction, file walk
│   │   ├── graph_builder.py     [P1] # networkx DiGraph, PageRank, cycle detection
│   │   ├── docker_engine.py     [P4] # Docker SDK: build w/ streaming logs, tag, prune
│   │   ├── kube_engine.py       [P4] # kubectl subprocess: apply, rollout, logs, undo
│   │   ├── tmux_recorder.py     [P4] # libtmux session, pane capture, replay file
│   │   ├── events.py            [P1] # EventBus: per-run asyncio.Queue + DB persistence
│   │   └── sandbox.py           [P1] # safe_join, validate_repo_path — SECURITY CRITICAL
│   │
│   ├── agents/                       # ── THE SWARM ──
│   │   ├── __init__.py          [P1]
│   │   ├── state.py             [P1] # DeployState TypedDict. FROZEN. See 01 §1.5
│   │   ├── base.py              [P1] # BaseAgent: name, emit(), run(state) contract
│   │   ├── architect.py         [P1] # language/framework detect + dependency graph
│   │   ├── smith.py             [P2] # LLM artifact generation + validation + repair
│   │   ├── templates.py         [P2] # deterministic Dockerfile/K8s fallbacks per stack
│   │   ├── warden.py            [P3] # Checkov + OPA orchestration, verdict logic
│   │   ├── redteam.py           [P3] # adversarial probes (LLM + fixed checklist)
│   │   ├── execution.py         [P4] # tmux + docker + kubectl, the deploy sequence
│   │   ├── oracle.py            [P6] # IsolationForest anomaly detection on pod metrics
│   │   ├── remediator.py        [P6] # kubectl rollout undo on anomaly
│   │   ├── finops.py            [P6] # cost estimate / AWS CE, NL cost Q&A
│   │   └── graph.py             [P5] # LangGraph StateGraph assembly. Glue only.
│   │
│   ├── api/
│   │   ├── __init__.py          [P1]
│   │   ├── health.py            [P1] # GET /health, GET /api/doctor
│   │   ├── projects.py          [P1] # CRUD + analyze
│   │   ├── runs.py              [P1] # trigger, get, list, artifacts, cancel
│   │   ├── ws.py                [P5] # WebSocket /ws/runs/{run_id}
│   │   ├── chat.py              [P5] # POST /api/chat — natural language router
│   │   └── costs.py             [P6] # GET /api/costs, POST /api/costs/query
│   │
│   ├── runner/
│   │   ├── __init__.py          [P1]
│   │   └── manager.py           [P1] # RunManager: spawn, track, cancel, persist. Semaphore.
│   │
│   ├── policies/                     # shipped as package data
│   │   ├── no_root_user.rego    [P3]
│   │   ├── no_sensitive_ports.rego [P3]
│   │   └── resource_limits.rego [P3]
│   │
│   ├── data/
│   │   ├── fewshot.jsonl        [P2] # 15–25 curated (stack → artifacts) pairs
│   │   ├── rate_card.json       [P6] # cloud $/vCPU-hr, $/GB-hr for local estimation
│   │   └── sample_cost_export.json [P6] # AWS Cost Explorer shape, for offline demo
│   │
│   └── web/
│       ├── templates/
│       │   ├── base.html        [P6] # layout, HTMX + CSS include
│       │   ├── index.html       [P6] # project list + register form
│       │   ├── project.html     [P6] # graph, run history
│       │   ├── run.html         [P6] # live timeline, artifacts, security, terminal
│       │   └── partials/        [P6] # HTMX fragments: _run_row, _finding, _log_line
│       └── static/
│           ├── app.css          [P6]
│           ├── app.js           [P6] # WebSocket client, log appender, graph render
│           └── vendor/          [P6] # htmx.min.js, cytoscape.min.js (vendored, no CDN)
│
├── tests/
│   ├── conftest.py              [P1] # tmp DEPLOYMINT_HOME, test client, sample repos
│   ├── fixtures/
│   │   ├── sample_fastapi/      [P1] # tiny real FastAPI app — the primary demo target
│   │   ├── sample_flask/        [P2]
│   │   ├── sample_express/      [P2]
│   │   └── sample_go/           [P2]
│   ├── test_config.py           [P1]
│   ├── test_sandbox.py          [P1] # path traversal attempts MUST be rejected
│   ├── test_architect.py        [P1]
│   ├── test_smith.py            [P2] # mocked LLM; asserts fallback fires on garbage
│   ├── test_warden.py           [P3] # known-bad Dockerfile MUST be blocked
│   ├── test_redteam.py          [P3]
│   ├── test_execution.py        [P4] # marked slow; needs docker
│   ├── test_graph.py            [P5]
│   ├── test_api_projects.py     [P1]
│   ├── test_api_runs.py         [P1]
│   └── test_finops.py           [P6]
│
└── scripts/
    ├── demo.sh                  [P7] # the exact demo sequence, scripted
    ├── reset.sh                 [P1] # wipe ~/.deploymint + recreate kind cluster
    └── build_fewshot.py         [P2] # scrape/curate few-shot pairs into fewshot.jsonl
```

---

## 2.1 The layering rule

```
api/  ──────────►  runner/  ──────────►  agents/  ──────────►  core/
 │                    │                     │                    │
 └──► schemas/        └──► db/              └──► agents/state.py └──► (stdlib, SDKs)
```

**Dependencies point one direction only.**

- `core/` imports nothing from `agents/`, `api/`, or `runner/`. It is pure infrastructure:
  "how to talk to Docker", "how to parse a file". You could lift it into another project.
- `agents/` imports `core/` and `state.py`. It contains the *reasoning*: "what makes a
  good Dockerfile", "what makes this insecure".
- `api/` imports `runner/` and `schemas/`. Route handlers should be **under 20 lines**.
  If a handler has an `if` chain, that logic belongs in `runner/` or `agents/`.
- `db/` is imported by `api/` and `runner/`. **Agents never touch the database.**
  Agents take state in, return state out. This is what makes them testable without a DB.

Violating the last rule is the most common way this codebase would rot. An agent that
does `db.query(Run)` cannot be unit-tested, cannot be reused by the CLI, and cannot be
moved into LangGraph cleanly.

---

## 2.2 `pyproject.toml` (complete, Phase 1)

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "deploymint"
version = "0.1.0"
description = "Local-first AI agent swarm for secure deployment: reads your code, writes your configs, proves they're safe, deploys them."
readme = "README.md"
requires-python = ">=3.11,<3.14"
license = { text = "Apache-2.0" }
authors = [{ name = "Haider Bukhari", email = "haider@ottooptics.io" }]
keywords = ["devops", "kubernetes", "docker", "ai-agents", "security", "finops"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Intended Audience :: Developers",
  "License :: OSI Approved :: Apache Software License",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Topic :: Software Development :: Build Tools",
  "Topic :: System :: Systems Administration",
]

dependencies = [
  "fastapi>=0.111",
  "uvicorn[standard]>=0.30",
  "sqlalchemy>=2.0",
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
  "langchain-ollama>=0.2",
  "scikit-learn>=1.5",
]

[project.optional-dependencies]
aws     = ["boto3>=1.34"]
router  = ["litellm>=1.44"]
rag     = ["chromadb>=0.5"]
forecast= ["prophet>=1.1"]
dev     = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.5", "mypy>=1.11", "httpx>=0.27"]
all     = ["deploymint[aws,router,rag,dev]"]

[project.scripts]
deploymint = "deploymint.cli:main"

[project.urls]
Homepage = "https://github.com/<you>/deploymint"
Issues   = "https://github.com/<you>/deploymint/issues"

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
select = ["E", "F", "I", "UP", "B", "S"]   # S = bandit security rules
ignore = ["S101"]                           # assert is fine in tests

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "slow: requires docker/kubernetes",
  "llm: requires a running Ollama",
]
```

### Why `checkov` is not in `dependencies`

Checkov pins conflicting versions of shared libs and would drag your install down.
It is invoked **as a subprocess**, so the user installs it separately (documented in the
README) or DeployMint degrades to OPA-only with a clear warning. The `deploymint doctor`
command tells them exactly what to run. This keeps `pip install deploymint` fast and
conflict-free — which matters enormously for open-source adoption.

---

## 2.3 `Makefile` (Phase 1)

```makefile
.PHONY: install run dev test lint fmt clean reset doctor

VENV := venv/bin

install:
	$(VENV)/pip install -e ".[dev]"

run:
	$(VENV)/deploymint server

dev:
	$(VENV)/deploymint server --reload

doctor:
	$(VENV)/deploymint doctor

test:
	$(VENV)/pytest -v -m "not slow"

test-all:
	$(VENV)/pytest -v

lint:
	$(VENV)/ruff check deploymint tests

fmt:
	$(VENV)/ruff format deploymint tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info

reset:
	rm -rf ~/.deploymint
	kind delete cluster --name deploymint || true
	kind create cluster --name deploymint
```

---

## 2.4 Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Agent class | `<Name>Agent` | `ArchitectAgent`, `SecurityWardenAgent` |
| Agent module | lowercase, no suffix | `architect.py`, `warden.py` |
| LangGraph node fn | `<name>_node` | `architect_node(state)` |
| Pydantic API model | `<Entity><Verb>` | `ProjectCreate`, `RunRead` |
| SQLAlchemy model | singular PascalCase | `Project`, `Run`, `AuditLog` |
| Event type | `<agent>.<phase>` | `architect.start`, `execution.log`, `warden.done` |
| Run ID | `run_` + 12 hex chars | `run_a3f8c21b9de0` |
| Image tag | `deploymint/<project>:<run_id>` | `deploymint/myapi:run_a3f8c21b9de0` |
| Rego package | `deploymint.<rule>` | `package deploymint.no_root_user` |
| Finding ID | `DM_<UPPER_SNAKE>` (ours), `CKV_*` (checkov) | `DM_ROOT_USER` |

Next: `03-data-model.md`.
