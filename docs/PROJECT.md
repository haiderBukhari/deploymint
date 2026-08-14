# DeployMint

**An AI agent swarm that reads your codebase, writes secure deployment configs, proves
they're safe, deploys them with a full audit trail, and tells you what it costs — all
through a single command, running entirely on your own machine.**

This document is the single place to understand the whole project: the problem, the
product, how it works end to end, the full tech stack, the architecture, every agent,
the security model, and what's real today versus planned. For the step-by-step build
plan, see [`docs/README.md`](docs/README.md) and the 17 documents it indexes.

---

## 1. The problem

Small engineering teams and solo developers lose a large fraction of their build time
hand-writing and maintaining deployment artifacts across multiple DSLs — Dockerfiles,
Kubernetes YAML, and (eventually) Terraform, Ansible, and CI workflows. Existing AI
coding assistants generate generic snippets that ignore repo-specific context:
dependency graphs, framework conventions, microservice boundaries. Security
misconfigurations — root containers, exposed ports, missing resource limits — are
caught only after deployment, if at all. Cloud bills spiral unnoticed because no one
tracks cost attribution per service.

There is no single tool that combines *understanding your actual code*, *generating
secure deployment artifacts*, *adversarially validating them before anything runs*,
*executing with a verifiable audit trail*, and *cost visibility* — in one flow, without
handing your source code to a third party.

---

## 2. The solution, in one command

```bash
docker compose up -d
```

That starts DeployMint — a small self-hosted application (its own web dashboard, its
own bundled database) — on your machine. Then:

```bash
deploymint up ./projects/my-app
```

or the equivalent action in the web dashboard at `http://localhost:8000`. What happens:

1. **Architect Agent** parses your codebase with `tree-sitter`, builds a real dependency
   graph with `networkx`, and ranks files by criticality with PageRank — deterministic,
   no LLM, sub-second.
2. **Artifact Smith** asks Claude to write a Dockerfile and Kubernetes manifests
   *specific to your repo* — right base image, right port, right entrypoint, non-root
   user, resource limits, health probes. If the API is unavailable or the output fails
   validation, a deterministic template takes over instead — the run never produces
   nothing.
3. **Security Warden** scans the generated artifacts with Checkov (550+ rules) and three
   custom OPA Rego policies — no root containers, no sensitive exposed ports, mandatory
   resource limits. This step is **entirely deterministic**; the LLM never decides
   whether something is safe.
4. **Red Team Agent** adversarially probes the artifacts for supply-chain smells,
   hardcoded secrets, reverse shells, and prompt-injection artifacts — an AI-generated
   config is exactly the kind of output that needs an adversarial second look, and this
   agent is the answer to "what if the source repo tried to manipulate the AI into
   writing something malicious."
5. **The gate.** If anything critical is found, the deploy is **blocked** with a
   specific, human-readable reason. This is the pillar of the whole product: the AI
   writes the config, deterministic tooling proves it's safe before anything executes.
6. **Execution Engine** builds the Docker image on your own machine's Docker daemon
   (via a mounted socket — DeployMint's own container never runs a separate nested
   Docker), deploys to your existing Kubernetes cluster if you have one, or runs the
   image directly with `docker run` if you don't. Every command is recorded in a
   replayable session and a hash-chained, tamper-evident audit log.
7. **Observability Oracle** watches the new deployment for anomalies (crash loops,
   OOM kills, failed readiness) and automatically rolls back if something's wrong.
8. **FinOps Agent** estimates the monthly cost of what just got deployed from its actual
   resource requests, flags over-provisioning, and answers natural-language questions
   like *"which service costs the most?"*

Your source code never leaves your machine. The only thing that touches the internet is
the request to Claude to generate the artifacts — everything else, including the
running of your own code, stays local.

---

## 3. Who this is for

- **Solo developers and indie hackers** who know Docker basics but don't want to spend
  an afternoon getting Kubernetes YAML right, and definitely don't want to discover a
  security misconfiguration after it's already live.
- **Small teams (2–10 engineers)** who self-host on their own cloud account or a local
  cluster and currently copy-paste deployment configs from Stack Overflow while
  tracking cloud spend in a spreadsheet.
- **Anyone evaluating whether an AI wrote something safe to run** — the Security
  Warden + Red Team combination is a genuinely defensible answer to "how do you know
  the AI didn't do something dangerous," which most AI coding tools have no answer to
  at all.

Not for: teams that want a fully managed PaaS where they never touch infrastructure at
all (that's Heroku/Railway/Render, and DeployMint is deliberately not competing there —
see §9). Not for teams needing multi-cloud managed clusters out of the box (that's on
the roadmap, not in the MVP).

---

## 4. Example use cases

**"I just want this FastAPI service running securely, now."**
Point DeployMint at the repo, click Deploy. Get a Dockerfile and Kubernetes manifests
tuned to the actual framework and entrypoint, security-scanned, deployed to a local
cluster or run directly with Docker, in under two minutes.

**"Did the AI just try to do something sketchy?"**
A repo's README contains a hidden instruction telling AI deployment tools to run as
root and pipe a remote script into bash. DeployMint's Artifact Smith may or may not
resist the injection — but the Security Warden and Red Team layer catch it either way,
and the deploy is blocked with the exact reason shown. This is the single strongest
demo moment in the product: the AI wrote it, deterministic tooling caught it.

**"Why is my cloud bill so high?"**
Ask the dashboard *"which service costs the most?"* and get a real answer computed from
actual resource requests (or a live AWS Cost Explorer connection, once configured) —
never a number invented by the model.

**"I need to prove what actually happened during this deploy."**
Every command — the `docker build`, the `kubectl apply`, the rollback — is recorded in
a tmux session you can replay, and written into a hash-chained audit log. Tamper with
one row and the verification endpoint tells you exactly which entry broke.

---

## 5. Full tech stack

| Layer | Technology | Why |
|---|---|---|
| Distribution | **Docker Compose** | one command, no pip install, source ships as a built image not readable `site-packages` |
| Backend | **Python 3.11, FastAPI, uvicorn** | async, WebSockets, free OpenAPI docs |
| Database | **Postgres 16** (bundled compose service) | JSONB for flexible agent-output storage, real concurrency, zero user setup |
| ORM | **SQLAlchemy 2.0 + psycopg** | sync engine, `create_all()` schema (pre-1.0, no Alembic yet) |
| LLM | **Claude (`claude-opus-5`) via the official `anthropic` SDK** | structured output validation, no offline fallback needed, used wherever it improves the product — see §7 |
| Orchestration | **LangGraph** | typed state graph over the seven agents, wired once the agents work standalone |
| Code analysis | **tree-sitter + `tree-sitter-language-pack`, NetworkX** | AST parsing, import graph, PageRank criticality ranking |
| Security scanning | **Checkov (550+ rules) + Open Policy Agent (Rego)** | both invoked as subprocesses, zero dependency conflicts |
| Execution | **Docker SDK + `kubectl`, via Docker-outside-of-Docker** | builds on the host's own daemon through a mounted socket; deploys to whatever cluster is reachable, or falls back to plain `docker run` |
| Session recording | **libtmux** | replayable terminal sessions for every deploy |
| Observability | **scikit-learn (`IsolationForest`)** + deterministic rollback rules | anomaly detection hook, backed by real crash/restart/OOM checks that do the actual work |
| Cost data | **boto3 (AWS Cost Explorer)**, local rate-card estimation | numbers always computed deterministically, LLM only phrases the answer |
| Web UI | **Jinja2 + HTMX, vendored JS (no CDN)** | server-rendered, no npm build step, ships inside the same image |
| CLI | **A thin `click` + `httpx` + `websockets` client** | pure HTTP/WS client with no agent logic — talks to the already-running container |

---

## 6. Architecture at a glance

```
Your machine
├── docker compose up -d
│   ├── deploymint-app (the built image)
│   │     FastAPI · LangGraph · all 7 agents · Claude client
│   │     Checkov · OPA · tree-sitter · docker CLI · kubectl — all baked in
│   │     mounts: ./projects → /workspace (your code)
│   │              /var/run/docker.sock (build on YOUR docker daemon)
│   │              ~/.kube/config (deploy to YOUR cluster, optional)
│   └── deploymint-db (postgres:16, bundled, zero config)
│
├── Browser → http://localhost:8000 (the dashboard — primary interface)
└── `deploymint` CLI (thin client, optional, talks to the same container)
                    │
                    └── HTTPS ──► api.anthropic.com  (the only thing "online")
```

Three deployment shapes exist for the built artifacts themselves, chosen automatically
per run:

1. **Your Kubernetes cluster**, if `~/.kube/config` is mounted and reachable — real
   pods, real Services, real rollout status.
2. **Plain `docker run`**, if no cluster is reachable — the same built image runs
   directly, so the product works for anyone who has Docker and nothing else.
3. *(Roadmap)* **A connected cloud account** — letting DeployMint provision or reach a
   managed cluster directly, for users who don't already have one.

Full diagrams and the request-by-request trace live in
[`docs/01-architecture.md`](docs/01-architecture.md).

---

## 7. How the AI is actually used

Three deterministic pillars never depend on the LLM at all — the Architect's analysis,
the Security Warden's pass/fail verdict, and the FinOps Agent's dollar figures. The LLM
is used freely everywhere it improves the product *without* touching those decisions:

| Component | What the LLM does | What stays deterministic |
|---|---|---|
| Architect | writes a plain-English summary of the codebase | the actual language/framework/graph detection |
| Artifact Smith | writes the Dockerfile + Kubernetes manifests | schema validation + template fallback |
| Security Warden | writes a plain-English explanation per finding | the pass/fail verdict — Checkov + OPA only |
| Red Team | adversarial critique of the generated artifacts | a fixed deterministic probe list that blocks on its own |
| Observability Oracle | explains *why* a rollback happened, in plain language | the anomaly detection and the rollback decision itself |
| FinOps | phrases the answer to a cost question | the dollar figures, always computed, never generated |

The rule that survives every one of these: **the LLM writes and explains; deterministic
code decides and computes.** This is the actual product thesis — "the AI writes the
config, deterministic tooling proves it's safe before anything runs" — and it's also
what makes the security story defensible against the obvious objection ("what if the AI
gets it wrong"): a wrong answer from the model gets caught by a scanner that doesn't
care what wrote the file.

---

## 8. The seven agents

1. **Architect** — tree-sitter + NetworkX dependency graph, language/framework
   detection, PageRank criticality ranking.
2. **Artifact Smith** — Claude-backed Dockerfile + Kubernetes manifest generation,
   validated against a strict schema, with a deterministic template fallback.
3. **Security Warden** — Checkov + three custom OPA Rego policies (no root user, no
   sensitive exposed ports, mandatory resource limits), the deterministic pass/fail
   gate.
4. **Red Team** — deterministic regex probes (hardcoded secrets, reverse shells,
   privileged containers, Docker socket mounts) plus an LLM adversarial critique layer.
5. **Execution Engine** — Docker-outside-of-Docker builds, `kubectl` or `docker run`
   deployment, tmux-recorded sessions, hash-chained audit log.
6. **Observability Oracle** — polls the new deployment, deterministic crash/restart/OOM
   triggers plus an `IsolationForest` anomaly hook, LLM-explained root cause.
7. **FinOps Agent** — resource-request-based cost estimation, a rate card, optional live
   AWS Cost Explorer, natural-language cost Q&A.

Full specs, algorithms, and exact prompts for all seven are in
[`docs/04-agents-spec.md`](docs/04-agents-spec.md).

---

## 9. Competitive positioning

| Alternative | What it does | What it doesn't do |
|---|---|---|
| GitHub Copilot / Cursor | generates code snippets | no full-repo understanding, no complete deployment package, no security gate |
| Terraform Cloud / Pulumi | manages infrastructure | requires you to already know how to write the config |
| Checkov / tfsec alone | scans for security issues | only after files are already written, no generation |
| Render / Railway / Heroku | fully managed deploys | you don't own the infrastructure, and it gets expensive at scale |

DeployMint's distinguishing claim: it reads your code, writes the config, proves it's
safe *before* anything runs, executes with a verifiable audit trail, and estimates the
cost — in one flow, on infrastructure you control the whole time.

---

## 10. What's real today vs. planned

| Capability | Status |
|---|---|
| Python / JS / Go / Java detection + dependency graph | ✅ working |
| Claude-backed Dockerfile + K8s Deployment/Service generation, template fallback | ✅ working |
| Checkov + 3 custom OPA policies + adversarial Red Team probes | ✅ working |
| Recorded execution with hash-chained audit log | ✅ working |
| Deploy to an existing cluster, or plain `docker run` if none exists | ✅ working |
| Cost estimation from manifests + natural-language cost queries | ✅ working |
| Terraform / Ansible / ArgoCD / GitHub Actions generation | 🚧 planned |
| Live AWS Cost Explorer connection (sample data today) | 🚧 planned |
| Connect a cloud account directly (no local kubeconfig needed) | 🚧 planned |
| `deploymint export` — write artifacts directly into your repo as a commit-ready diff | 🚧 planned, high value, low effort |

---

## 11. Security model, briefly

- The app only ever reads or writes under the one mounted projects directory — nothing
  else on the host is reachable, and the sandbox check is a hard allowlist, not a
  denylist.
- The mounted Docker socket is **root-equivalent host access**, stated plainly rather
  than hidden — the same trust boundary every CI system that builds Docker images
  already accepts (Jenkins, GitLab Runner). The compose file binds the dashboard to
  `127.0.0.1` by default for exactly this reason.
- Generated artifacts are written next to the project they came from, never overwriting
  the user's actual files, unless explicitly exported.
- The audit log is **tamper-evident** (a SHA-256 hash chain), not cryptographically
  signed — an honest distinction, stated explicitly rather than oversold.
- A poisoned repository attempting prompt injection against the Artifact Smith is
  exactly the scenario the Security Warden + Red Team layer exists to catch, regardless
  of whether the LLM itself resists the injection.

Full detail in [`docs/01-architecture.md`](docs/01-architecture.md) §1.7.

---

## 12. Monetization shape (from the original proposal, still applicable)

- **Free, self-hosted**: everything described in this document — drives adoption.
- **Team tier** (future): shared audit logs, a hosted dashboard, centralized policy
  packs — the natural next step once the local product has real usage.
- **Enterprise**: on-premise, custom security policies, SSO.
- **API usage**: pay-per-call for teams wanting to integrate artifact generation into
  an existing CI/CD pipeline.

---

## 13. Full documentation index

This file is the summary. The complete, buildable specification — every file, every
API endpoint, every agent's exact algorithm and prompts, phase-by-phase build
instructions with acceptance tests, the testing strategy, and a full log of every
architectural direction this project considered and rejected — lives in
[`docs/`](docs/README.md):

| # | Doc | Covers |
|---|---|---|
| 00 | [Prerequisites](docs/00-prerequisites.md) | End-user setup (Docker only) vs. image-build setup |
| 01 | [Architecture](docs/01-architecture.md) | Container topology, request flow, locked decisions |
| 02 | [Repo Layout](docs/02-repo-layout.md) | Every file, the Dockerfile, the compose file |
| 03 | [Data Model](docs/03-data-model.md) | Postgres schema, API surface |
| 04 | [Agent Specs](docs/04-agents-spec.md) | All 7 agents, algorithms, exact prompts |
| 05–11 | Phase 1–7 build docs | Day-by-day implementation with full code and acceptance tests |
| 12 | [Testing Strategy](docs/12-testing-strategy.md) | Test pyramid, fixtures, CI |
| 13 | [Risks & Cutlines](docs/13-risks-and-cutlines.md) | What could kill this project, and what to cut first |
| 14 | [Command Reference](docs/14-command-reference.md) | Every command, one page |
| 15 | [Learning Path](docs/15-learning-path.md) | What to learn, in what order |
| 16 | [Decisions Log](docs/16-decisions-log.md) | Every architecture direction considered, and why it changed |

Start with `docs/README.md` if you're about to build this.
