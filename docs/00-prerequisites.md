# 00 — Prerequisites

## 0.1 Machine audit (run 2026-08-12 on this Mac)

| Requirement | Found | Verdict | Action |
|---|---|---|---|
| Python 3.11 venv at `./venv` | `venv/bin/python3.11` | ✅ **use it** | none |
| System `python3` | **3.15.0a6** | ⚠️ **never use** | alpha; `checkov`, `tree-sitter`, `chromadb` have no wheels |
| Docker daemon | 29.2.0 (linux VM) | ✅ | none |
| `kubectl` | `/usr/local/bin/kubectl` | ✅ | none |
| Local K8s cluster | **none** | ❌ | install `kind` (§0.3) |
| Ollama | `/opt/homebrew/bin/ollama` | ✅ | none |
| `llama3.1:8b` | pulled (4.9 GB) | ✅ | none |
| `nomic-embed-text` | pulled (274 MB) | ✅ | used later for RAG |
| `tmux` | **not found** | ❌ | `brew install tmux` (§0.3) |
| `git` | `/usr/bin/git` | ✅ | none |
| Repo is a git repo | **no** | ⚠️ | `git init` recommended (§0.5) |

### ⚠️ The single most important line in this document

Your shell's `python3` is **Python 3.15.0a6**, a pre-release. Half of DeployMint's
dependency tree ships no wheels for it and will attempt (and fail) source builds.

**Always activate the venv first. Every command in every doc assumes it is active.**

```bash
source /Users/haiderbukhari/Public/DeployMint/venv/bin/activate
```

Verify — this must print `3.11.x`:

```bash
python -V
```

---

## 0.2 Why each tool is needed

| Tool | Used by | Fails how if missing |
|---|---|---|
| Python 3.11 | everything | dependency resolution errors, C-extension build failures |
| Docker | Execution Engine — builds images from generated Dockerfile | `docker.errors.DockerException` on server start |
| kind (or Docker Desktop K8s) | Execution Engine — `kubectl apply` target | deploy step has nowhere to go; demo stops at "image built" |
| kubectl | Execution Engine | cannot apply manifests |
| Ollama | LLM layer — default model backend | Artifact Smith silently falls back to templates (still works, but no AI story) |
| tmux | Execution Engine — recorded sessions | the *auditability* pillar of the pitch disappears |
| git | Architect Agent — ignores `.git`, reads repo metadata | minor; degrades gracefully |
| OPA (`opa` binary) | Security Warden — Rego policy evaluation | Rego rules skipped; Checkov still runs |
| Node.js (optional) | only if you build a React UI instead of server-rendered | n/a for MVP |

---

## 0.3 Install the missing pieces

Run these **once**. They are outside the venv (system tools).

```bash
brew install tmux kind opa
```

Then create the cluster DeployMint will deploy into:

```bash
kind create cluster --name deploymint
```

Verify the cluster is live and `kubectl` points at it:

```bash
kubectl cluster-info --context kind-deploymint
```

Expected output contains `Kubernetes control plane is running at https://127.0.0.1:<port>`.

### Alternative to kind: Docker Desktop Kubernetes

If you prefer not to install `kind`: open Docker Desktop → Settings → Kubernetes →
**Enable Kubernetes** → Apply & Restart. Then your context is `docker-desktop` instead
of `kind-deploymint`. Set it in config later:

```bash
kubectl config use-context docker-desktop
```

**Recommendation: use `kind`.** It is disposable (`kind delete cluster --name deploymint`
resets everything in seconds), which matters a lot when you are debugging a deploy loop
at 2am on Day 9.

### One kind-specific gotcha you must know now

`kind` runs its own containerd, **separate from your Docker daemon**. An image you build
with `docker build` is *not* visible to the cluster. You must load it:

```bash
kind load docker-image deploymint/myapp:latest --name deploymint
```

This single command is the #1 cause of `ErrImagePull` in local K8s demos. The Execution
Engine handles it automatically (see `08-phase-4-execution.md` §8.5), but know why it's there.

---

## 0.4 Install Python dependencies

```bash
source /Users/haiderbukhari/Public/DeployMint/venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

Full dependency set, grouped by what it serves. Install in this order — Checkov drags in
a large tree and is the most likely to conflict, so surface that early:

```bash
pip install "checkov>=3.2.0"
```

```bash
pip install "fastapi>=0.111" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" "pydantic>=2.7" "pydantic-settings>=2.3" "python-multipart>=0.0.9" "jinja2>=3.1"
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
pip install "langgraph>=0.2" "langchain-core>=0.3" "langchain-ollama>=0.2" "litellm>=1.44"
```

```bash
pip install "scikit-learn>=1.5" "boto3>=1.34"
```

Dev tools:

```bash
pip install "pytest>=8.0" "pytest-asyncio>=0.23" "ruff>=0.5" "mypy>=1.11"
```

### Notes on specific packages

- **`tree-sitter-language-pack`** — do *not* install `tree-sitter-python` etc. individually
  and do *not* try to compile grammars yourself. The language pack ships prebuilt binaries
  for ~100 languages behind one API. This saves you a full day.
- **`checkov`** pins older versions of some libs and **will** print resolver errors.
  Install it **first** (as above) and let the rest resolve around it. Two conflicts are
  expected and have been **verified benign** — see §0.7.
- **`chromadb`** is deliberately **not** in this list. RAG few-shot is a stretch goal;
  chromadb is heavy and pulls conflicting deps. Add it only in Phase 8.
- **`prophet`** is deliberately **not** in this list. It needs a compiler toolchain and
  takes ~10 minutes to install. The Observability Oracle uses `IsolationForest` from
  scikit-learn only. Prophet is a stretch goal.

### The three requirements files (created 2026-08-12)

| File | Contents | Use |
|---|---|---|
| `requirements.txt` | direct runtime deps only, lower bounds | mirrors `[project.dependencies]`; edit this by hand |
| `requirements-dev.txt` | `-r requirements.txt` + pytest, ruff, mypy, build, checkov | dev checkout |
| `requirements.lock.txt` | all 165 packages, exact `==` pins | reproduce this exact environment |

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

The working directory is not a git repo yet. Do this before writing code — you want
checkpoints available from the first commit.

```bash
cd /Users/haiderbukhari/Public/DeployMint && git init && git branch -M main
```

Create `.gitignore` at the repo root:

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
.deploymint/
*.db
*.sqlite3
.env
.DS_Store
```

`.deploymint/` is the runtime home directory (DB, logs, artifacts). It must never be
committed — it contains user repo paths and possibly cloud cost data.

---

## 0.6 The `deploymint doctor` command (build this in Phase 1)

This is not optional polish. It is how every future user (and you, on Day 9, at 2am)
finds out *which* prerequisite broke. Spec:

| Check | Method | Failure message must say |
|---|---|---|
| Python version | `sys.version_info >= (3, 11)` and `< (3, 14)` | which version you're on, and to activate the venv |
| Docker daemon | `docker.from_env().ping()` | "Docker daemon unreachable — is Docker Desktop running?" |
| kubectl binary | `shutil.which("kubectl")` | install hint |
| K8s cluster reachable | `kubectl cluster-info --request-timeout=3s` | "No cluster. Run: `kind create cluster --name deploymint`" |
| kind binary | `shutil.which("kind")` | only a warning if the context isn't a kind context |
| Ollama reachable | `GET {OLLAMA_BASE_URL}/api/tags` | "Ollama not running. Run: `ollama serve`" |
| Default model pulled | model name present in `/api/tags` response | "Run: `ollama pull llama3.1:8b`" |
| tmux binary | `shutil.which("tmux")` | "brew install tmux — execution recording disabled without it" |
| OPA binary | `shutil.which("opa")` | warning only; Rego checks skipped |
| Home dir writable | `os.access(DEPLOYMINT_HOME, os.W_OK)` | path + permissions |
| networkx not downgraded | `networkx.__version__ >= "3.3"` | "checkov's stale pin downgraded networkx — run `pip install 'networkx>=3.3'`" (see §0.7) |

Output format: a Rich table, one row per check, `✓` green / `!` yellow / `✗` red.
Exit code `0` if no red, `1` if any red. **Warnings (yellow) never fail the exit code** —
tmux and OPA degrade gracefully.

---

## 0.7 The Checkov resolver conflicts (expected — verified benign)

`pip` will print this, twice, during the install:

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
§4.3 fired correctly (`CKV_K8S_10/11/12/13/20/23/28/37`, `CKV_DOCKER_*`). Checkov also
discovered all 550+ checks on `importlib-metadata` 8.9.0, so its entry-point loading is
unaffected.

You need `networkx>=3.3` for the Architect Agent; Checkov's `<2.7` pin would drag you
back to a 2021 release. **Keep 3.6.1.**

### Protect this state

The risk is not the warning — it is a **future `pip install` silently downgrading
networkx back to 2.6.3** to satisfy the pin, which would regress the Architect Agent.
Two defenses:

```bash
pip freeze > requirements.lock.txt
```

Then, after any future install that touches these packages, confirm nothing moved:

```bash
python -c "import networkx; assert networkx.__version__ >= '3.3', f'DOWNGRADED to {networkx.__version__}'; print('networkx ok:', networkx.__version__)"
```

Add that assertion to `deploymint doctor` (§0.6) as a check named *"networkx not
downgraded by checkov"*. It costs one line and catches a regression that would otherwise
show up as a confusing graph bug on Day 8.

### If Checkov ever does break

Because of decision #9 (`01-architecture.md`), the Security Warden invokes Checkov as a
**subprocess**, never as an import. So Checkov does not have to live in this venv at all:

```bash
pipx install checkov
```

The Warden calls the `checkov` binary on `PATH` and parses stdout either way — nothing in
the code changes. That is the escape hatch, and it is why the subprocess design was chosen
up front rather than as a workaround.

---

## 0.8 Acceptance test for Phase 0

Every one of these must succeed before you write application code:

```bash
source venv/bin/activate && python -V && python -c "import fastapi, sqlalchemy, langgraph, networkx, docker, libtmux, tree_sitter; print('imports ok')"
```

```bash
docker ps >/dev/null && kubectl get nodes && curl -s localhost:11434/api/tags | head -c 200 && which tmux opa
```

When both commands run clean, tick `Phase 0` in `README.md` and go to
`01-architecture.md`.
