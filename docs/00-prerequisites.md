# 00 — Prerequisites

This doc has two audiences that must not be confused:

- **§0.1 — the end user.** Whoever runs `docker compose up`. Their list is two items.
- **§0.2 onward — you, building the image.** Everything Checkov/tree-sitter/kind-related
  that used to be a 10-step end-user setup checklist is now **internal to the Dockerfile**.
  It happens once, when you build the image, and the end user never sees any of it.

This split is the actual payoff of the Docker Compose decision — read `01-architecture.md`
§1.1 if you haven't yet.

---

## 0.1 End-user prerequisites (the whole list)

| Requirement | Why |
|---|---|
| Docker Desktop (or Docker Engine + Compose plugin) | runs the app, builds images via the mounted socket, runs Postgres |
| A directory to point at their code | mapped in `.env` as `DEPLOYMINT_PROJECTS_DIR` |

That's it. No Python, no Postgres install, no Checkov, no `kind`, no Ollama, no API key
management beyond pasting one value into `.env`.

```bash
git clone <this-repo> deploymint && cd deploymint
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY and DEPLOYMINT_PROJECTS_DIR
docker compose up -d
```

Open `http://localhost:8000`.

**One optional item:** if the user wants real Kubernetes deploys rather than plain
`docker run`, they need a reachable cluster and a `~/.kube/config` — `kind`,
Docker Desktop's built-in Kubernetes, or a real cloud cluster all work. This is optional;
see `01-architecture.md` §1.4 decision 12 for the fallback behavior.

---

## 0.2 Machine audit — the build machine (yours, not the end user's)

Everything below this line describes **your** development machine while you write the
Dockerfile and the app. Verified on this Mac, 2026-08-12:

| Requirement | Found | Verdict | Action |
|---|---|---|---|
| Python 3.11 venv at `./venv` | `venv/bin/python3.11` | ✅ **use it for local dev** | none |
| System `python3` | **3.15.0a6** | ⚠️ **never use** | alpha; `checkov`, `tree-sitter`, `chromadb` have no wheels |
| Docker daemon | 29.2.0 (linux VM) | ✅ | none |
| `kubectl` | `/usr/local/bin/kubectl` | ✅ | none — dev-time only, for testing the deploy path |
| Local K8s cluster (`kind`) | none initially | needed for dev | `kind create cluster --name deploymint` |
| `tmux` | not found initially | needed for dev | `brew install tmux` |
| `git` | `/usr/bin/git` | ✅ | none |
| OPA | not found initially | needed for dev | `brew install opa` |

**None of this matters to the end user.** It matters because the Dockerfile you write
needs to install the same things *inside the image* — Python 3.11, Checkov, OPA, tree-
sitter grammars, `kubectl` — and you want to develop against the same versions you'll
bake in, so surprises show up on your machine, not in the built image.

### The single most important line, still true inside the image

The image's base is `python:3.11-slim` (or similar), never a `latest` tag that could
silently move to a newer, wheel-incompatible Python. Pin it explicitly in the Dockerfile.

---

## 0.3 What goes in the Dockerfile — and why each piece is there

| Tool | Baked in because | Fails how if missing from the image |
|---|---|---|
| Python 3.11 | everything | dependency resolution errors, C-extension build failures |
| `tree-sitter-language-pack` | Architect Agent's AST parsing | import extraction breaks silently |
| `checkov` | Security Warden | scan step fails; the app must fail closed (see `07-phase-3-security.md`) |
| `opa` binary | Security Warden's Rego rules | Rego checks skipped; same fail-closed rule applies |
| `kubectl` binary | Execution Engine | cannot apply manifests to a mounted kubeconfig |
| `docker` CLI (client only — talks to the mounted socket) | Execution Engine | cannot build images |
| `tmux` | Execution Engine's recorded sessions | the auditability pillar of the pitch disappears |
| `git` | Architect Agent metadata reads | minor; degrades gracefully |

Build this once, verify it, and it never needs to be re-verified by an end user again —
that verification work has been done for them by virtue of being in the image.

---

## 0.4 Local dev environment (for building the app itself)

```bash
source /Users/haiderbukhari/Public/DeployMint/venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

Install in this order — Checkov drags in a large tree and is the most likely to
conflict, so surface that early, exactly as it will inside the image build:

```bash
pip install "checkov>=3.2.0"
```

```bash
pip install "fastapi>=0.111" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" "psycopg[binary]>=3.1" "pydantic>=2.7" "pydantic-settings>=2.3" "python-multipart>=0.0.9" "jinja2>=3.1"
```

```bash
pip install "click>=8.1" "rich>=13.7" "httpx>=0.27" "pyyaml>=6.0" "websockets>=12.0"
```

```bash
pip install "tree-sitter>=0.22" "tree-sitter-language-pack>=0.2" "networkx>=3.3"
```

```bash
pip install "docker>=7.0" "libtmux>=0.37"
```

```bash
pip install "langgraph>=0.2" "langchain-core>=0.3" "anthropic>=0.69"
```

```bash
pip install "scikit-learn>=1.5" "boto3>=1.34"
```

Dev tools:

```bash
pip install "pytest>=8.0" "pytest-asyncio>=0.23" "ruff>=0.5" "mypy>=1.11"
```

### Notes on specific packages

- **`tree-sitter-language-pack`** — do *not* install `tree-sitter-python` etc.
  individually and do *not* compile grammars yourself. Prebuilt binaries for ~100
  languages, one API. Saves a full day, in dev and in the image build alike.
- **`checkov`** pins older versions of some libs and **will** print resolver errors.
  Install it **first** and let the rest resolve around it. Two conflicts are expected and
  have been **verified benign** — see §0.6.
- **`psycopg[binary]`** — the Postgres driver. `[binary]` avoids needing build tools for
  `libpq` inside the image; use the pure-C extension build only if you have a specific
  reason to.
- **`langchain-ollama`, `litellm`, `chromadb`, `prophet`** are **not** in this list.
  Ollama is gone as a fallback provider — the product is online by design (see
  `01-architecture.md` §1.9). A model router and RAG few-shot remain stretch goals; add
  them only if they earn their place later.

### The three requirements files

| File | Contents | Use |
|---|---|---|
| `requirements.txt` | direct runtime deps only, lower bounds | mirrors `[project.dependencies]`; this is also what `pip install` runs **inside the Dockerfile** |
| `requirements-dev.txt` | `-r requirements.txt` + pytest, ruff, mypy, checkov | local dev checkout only — not copied into the image |
| `requirements.lock.txt` | all packages, exact `==` pins | reproduce this exact environment, and pin the same versions in the Dockerfile's `pip install -r` step |

```bash
pip install -r requirements-dev.txt
```

Regenerate the lock after any dependency change:

```bash
pip freeze > requirements.lock.txt
```

**`pip install --dry-run -r requirements-dev.txt` will hang** (>2 min) — pip backtracks
hard against Checkov's stale pins. That is a resolver performance issue, not a broken
file. To check the environment instead, assert every requirement is already satisfied:

```bash
python -c "
from importlib.metadata import version
from packaging.requirements import Requirement
import pathlib
bad=[]
for f in ('requirements.txt','requirements-dev.txt'):
    for ln in pathlib.Path(f).read_text().splitlines():
        ln=ln.split('#')[0].strip()
        if not ln or ln.startswith('-r'): continue
        r=Requirement(ln); v=version(r.name)
        if r.specifier and not r.specifier.contains(v, prereleases=True): bad.append(f'{ln} -> {v}')
print(bad or 'all satisfied')"
```

---

## 0.5 Initialize git

```bash
cd /Users/haiderbukhari/Public/DeployMint && git init && git branch -M main
```

`.gitignore`:

```
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
*.egg-info/
dist/
build/
.env
projects/*/.deploymint/
.DS_Store
```

`.env` holds the Anthropic key — never commit it. `projects/*/.deploymint/` is generated
run output living alongside the user's own code — also never committed. Ship `.env.example`
with placeholder values so a fresh checkout has an obvious template to copy.

---

## 0.6 The Checkov resolver conflicts — expected, verified benign

`pip` will print this, twice, both locally and inside the image build:

```
ERROR: pip's dependency resolver does not currently take into account all the
packages that are installed. This behaviour is the source of the following
dependency conflicts.
checkov 3.3.10 requires networkx<2.7, but you have networkx 3.6.1 which is incompatible.
checkov 3.3.10 requires importlib-metadata<8.0.0, but you have importlib-metadata 8.9.0 which is incompatible.
```

**Both pins are stale. Ignore them.** Verified on this machine, 2026-08-12:

| Scan | Result | stderr | Parse errors |
|---|---|---|---|
| `checkov --framework dockerfile` | 2 passed / 4 failed | clean | 0 |
| `checkov --framework kubernetes` | 69 passed / 20 failed | clean | 0 |

The Kubernetes run included graph-based `CKV2_*` checks — those are precisely the ones
that exercise networkx — and every check ID in the severity map of `04-agents-spec.md`
§4.3 fired correctly. Checkov also discovered all 550+ checks on `importlib-metadata`
8.9.0, so its entry-point loading is unaffected.

You need `networkx>=3.3` for the Architect Agent; Checkov's `<2.7` pin would drag you
back to a 2021 release. **Keep 3.6.1**, and pin it explicitly in `requirements.txt` so a
future `pip install` inside the image build can't silently satisfy Checkov's pin instead.

If Checkov ever does genuinely conflict with something else in the app's dependency tree,
the escape hatch is to install it into an isolated location inside the image (`pipx` or a
separate venv layer) and invoke the `checkov` binary as a subprocess either way — nothing
in the app code changes, because Security Warden already calls it as a subprocess by
design (`01-architecture.md` decision 10).

---

## 0.7 Dev-only acceptance test

Run this on your own machine before writing the Dockerfile, so you know the app works
before you containerize it:

```bash
source venv/bin/activate && python -V && python -c "import fastapi, sqlalchemy, psycopg, langgraph, networkx, docker, libtmux, tree_sitter, anthropic; print('imports ok')"
```

```bash
docker ps >/dev/null && kubectl get nodes && which tmux opa && python -c "import os,sys; sys.exit(0 if os.getenv('ANTHROPIC_API_KEY') else print('set ANTHROPIC_API_KEY in your shell for local dev'))"
```

The real acceptance test — `docker compose up -d` on a machine with nothing but Docker —
comes in `11-phase-7-polish-demo.md` once the image exists.
