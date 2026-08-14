# 11 — Phase 7: Packaging, Docs & Demo (Day 14)

**Goal:** `docker compose up -d` on a machine with nothing but Docker installed produces
a working dashboard, the README makes someone want to try it, and you can run a 5-minute
demo without touching a keyboard shortcut you haven't rehearsed.

---

## Step 7.1 — Build and test the image on a clean environment

```bash
docker compose build
```

```bash
docker images | grep deploymint
```

Now the real test — as close as you can get to a machine that has never seen your source
tree. If you have access to a second machine or a fresh VM, use it; otherwise, a clean
checkout in a scratch directory with a fresh `docker compose build --no-cache` is the
next best thing:

```bash
git clone . /tmp/dm-clean && cd /tmp/dm-clean
```

```bash
cp .env.example .env && echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env && echo "DEPLOYMINT_PROJECTS_DIR_HOST=$(pwd)/projects" >> .env
```

```bash
mkdir -p projects && docker compose up -d --build
```

```bash
curl -s localhost:8000/health
```

**What breaks here, every time:** package data. Your `.rego` policies, `fewshot.jsonl`,
`rate_card.json`, and `web/templates/` are not Python modules, so `setuptools` skips them
inside the image build unless `[tool.setuptools.package-data]` lists them
(`02-repo-layout.md` §2.6). Verify from inside the running container:

```bash
docker compose exec app python -c "
from importlib.resources import files
p = files('deploymint')
for rel in ['policies/no_root_user.rego','data/rate_card.json','data/fewshot.jsonl','web/templates/run.html','web/static/app.js']:
    print(('OK  ' if (p/rel).is_file() else 'MISS'), rel)
"
```

Every line must say `OK`. If any say `MISS`, fix `package-data` and rebuild the image —
`docker compose build` caches layers, so a fix here is fast to iterate on.

**Also verify:** anywhere you used `Path(__file__).parent / "policies"` instead of
`importlib.resources.files()` will work in a source checkout and break subtly once the
package is actually installed via `pip install -e .` inside the Dockerfile's build
context rather than run directly from the source tree. Grep for `__file__` and convert.

---

## Step 7.2 — README

The README is the product's storefront. Structure, in order:

1. **One line + a GIF.** The GIF is the single highest-value asset in the repo. Someone
   decides whether to try DeployMint in about four seconds.
2. **What it does** — 4 bullets, no adjectives.
3. **Install** — the actual number of commands (four: clone, copy `.env`, add the key,
   `docker compose up -d`). Do not round this down; overselling "one command" when it's
   really four erodes trust the moment someone tries it.
4. **Quickstart** — the 60-second path to a running pod.
5. **How it works** — the container topology diagram from `01-architecture.md` §1.2.
6. **What's real vs. what's next** — honest scope table.
7. **Configuration** — the `.env` table from `02-repo-layout.md` §2.5.
8. **Requirements** — Docker + Docker Compose. That's the whole list for the end user;
   see `00-prerequisites.md` §0.1.
9. **Contributing / License.**

### Recording the GIF

```bash
brew install asciinema agg
```

```bash
asciinema rec demo.cast --cols 100 --rows 30
```

Run `deploymint up ./projects/fastapi-app` (or drive it from the web UI — either is a
fine demo, and the web UI arguably shows off the product's actual primary interface
better, per `01-architecture.md` §1.4 decision 13), then exit and convert:

```bash
agg demo.cast demo.gif --theme monokai --font-size 16
```

Keep it under 30 seconds and under 3 MB. Cut the dead time during the docker build with
`asciinema`'s idle-time limit:

```bash
asciinema rec demo.cast --idle-time-limit 1.5
```

### The scope table — include this, it builds trust

```markdown
## What's real today

| Capability | Status |
|---|---|
| Python / JS / Go / Java detection + dependency graph | ✅ working |
| Dockerfile + K8s Deployment/Service generation (Claude-backed, template fallback) | ✅ working |
| Checkov + 3 custom OPA policies + adversarial probes | ✅ working |
| Recorded execution with hash-chained audit log | ✅ working |
| Deploys to your existing Kubernetes cluster, or plain `docker run` if you have none | ✅ working |
| Cost estimation from manifests + NL cost queries | ✅ working |
| Terraform / Ansible / ArgoCD / GitHub Actions generation | 🚧 planned |
| Live AWS Cost Explorer connection | 🚧 planned (sample data today) |
| Managed cloud clusters (EKS/GKE/AKS) reachable via a connected account, not just a local kubeconfig | 🚧 planned |
```

Listing what does *not* work yet is not a weakness. It is the difference between a
project people trust and one they bounce off after the first broken promise.

---

## Step 7.3 — Example repos

```
projects/                # this IS your DEPLOYMINT_PROJECTS_DIR — see 01-architecture.md §1.8
├── fastapi-app/          # the flagship — must deploy cleanly every time
├── express-app/
├── go-service/
└── README.md             # "pick one and run: deploymint up ./projects/fastapi-app"
```

Unlike the original pip-distributed design, these live **directly under the mounted
projects directory** — they're not a separate `examples/` folder the user copies from,
they're already in the one place DeployMint can see. Each must have a `/health`
endpoint and deploy without a single manual fix.

---

## Step 7.4 — The demo script

`scripts/demo.sh`, but **know it by heart** — do not read from the script live.

### Pre-flight (run 10 minutes before, not during)

```bash
docker compose down -v && docker compose up -d --build
```

```bash
kind delete cluster --name deploymint-demo 2>/dev/null; kind create cluster --name deploymint-demo
```

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml && kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

```bash
docker compose exec app python -c "from deploymint.core import llm; print(__import__('asyncio').run(llm.health()))"
```

```bash
docker pull python:3.11-slim
```

The last two matter enormously. **A cold connection to the Anthropic API adds a few
seconds of first-call latency** — trivial compared to the old cold-Ollama problem, but
still worth a warm-up call so the first real generation in your demo isn't the first
network round trip too. A cold `python:3.11-slim` pull adds 20 seconds to your build.
Both are pure dead air in a demo. Warm them.

### The 5-minute run

**0:00 — The problem (30s).** No slides. Show a real repo.
> "This is a FastAPI service. To deploy it I need a Dockerfile, a Deployment, a Service,
> resource limits, a security context, and probes. That's 45 minutes of YAML I'll get
> subtly wrong. And I won't know it's wrong until it's running."

**0:30 — One command, already running (15s).**
> "DeployMint is already up — `docker compose up -d`, that's the whole install."
```bash
deploymint up ./projects/fastapi-app
```

**0:45 — Architect (30s).** Point at the terminal.
> "Tree-sitter parsed the AST, built an import graph, and ranked files by PageRank.
> `db.py` is most critical — two modules depend on it. No LLM involved; this is
> deterministic static analysis."

**1:15 — Smith (45s).** The Dockerfile appears — this should take seconds, not the
20-30s an 8B local model needed.
> "Claude wrote this — running as this container's own LLM client, over the internet,
> the only thing here that touches the network besides pulling base images. Note what it
> got right: multi-stage build, pinned base image, non-root UID 10001, dependency layer
> cached before source. That's specific to *this* repo, not a template."

**2:00 — Warden + Red Team (45s).**
> "Checkov's 550 rules plus three custom OPA policies I wrote. Passed. But watch this."

**2:45 — THE MOMENT (75s).** Switch to the poisoned repo.
```bash
deploymint up ./projects/poisoned-repo
```
> "This repo's README has a prompt injection targeting AI deployment tools. It's telling
> the model to run root, pipe a remote script into bash, and open port 22."

Wait for the block. Read the reason aloud.
> "The AI generated it. The security layer caught it. **That's the whole architecture:
> the model writes the config, deterministic tooling proves it's safe before anything
> runs.** No AI code assistant does this — they hand you a snippet and wish you luck."

**4:00 — Execution + audit (45s).** Back to the good run, now deployed.
```bash
kubectl get pods -l managed-by=deploymint
```
```bash
curl -s localhost:8081/health
```
> "Real pod, real traffic. And every command is recorded —"
```bash
curl -s localhost:8000/api/runs/$RUN/audit/verify
```
> "Hash-chained audit log, verifiable. Let me break it."
```bash
docker compose exec db psql -U deploymint -c "UPDATE audit_logs SET output='nothing happened' WHERE run_id='$RUN' AND seq=3"
```
```bash
curl -s localhost:8000/api/runs/$RUN/audit/verify
```
> "Tamper detected at entry 3."

**4:45 — FinOps (15s).**
```bash
curl -s -X POST localhost:8000/api/costs/query -d '{"question":"which service costs the most?"}' -H 'content-type: application/json'
```

**5:00 — Close.**
> "Reads your code, writes secure configs, proves they're safe, deploys them with a
> receipt, and tells you what it costs. `docker compose up`. Your code, your build, your
> cluster — the only thing that leaves this machine is the request to write the config."

### Demo rules

| Rule | Why |
|---|---|
| Terminal font ≥ 18pt | nobody can read 12pt on a projector |
| Rehearse the whole thing 3× | you will discover a broken step on run 2 |
| Record a backup video | live demos fail; a video is not a failure, it's a fallback |
| Never say "it should work" | if you're unsure, don't show it |
| Have the poisoned-repo output pre-captured | this is your best moment — protect it |
| Pin your `RUN_ID` in a shell var | typing a 12-char hex ID live is a guaranteed stumble |
| Have a stable internet connection at the venue | the Smith and Red Team LLM calls are real network requests now — test the venue's wifi beforehand, and have the template-fallback path in your back pocket as a talking point if it's flaky |

---

## Step 7.5 — Final checklist

```
CODE
[ ] `ruff check deploymint tests` clean
[ ] `pytest -m "not slow"` green with no ANTHROPIC_API_KEY set (proves the fallback paths)
[ ] `pytest` (including slow) green with Docker + a cluster available
[ ] No `print()` left in library code — Rich console or logging only
[ ] No hardcoded absolute paths (grep for /Users/, /home/)
[ ] No API keys, tokens, or personal paths committed (check .env is gitignored, not .env.example)
[ ] `grep -rn "__file__" deploymint/` → all converted to importlib.resources

PACKAGING
[ ] `docker compose build` succeeds from a clean checkout
[ ] All package data present inside the running container (§7.1 check)
[ ] `docker compose up -d` on a machine with only Docker + Compose works end to end
[ ] `.env.example` has every variable the app reads, with comments
[ ] The thin CLI's `--help` is readable and complete

DOCS
[ ] README with GIF, install (the real 4 commands), quickstart, honest scope table
[ ] LICENSE (Apache 2.0)
[ ] projects/ has 3 working example repos
[ ] CONTRIBUTING.md (even a short one)
[ ] The docs/ directory is in the repo — it shows your thinking
[ ] docs/16-decisions-log.md is up to date — it's the fastest way for a reviewer to
    understand why the architecture looks the way it does

DEMO
[ ] Compose stack rebuilt fresh, Postgres volume wiped
[ ] Dev kind cluster fresh, metrics-server installed
[ ] LLM warm-up call made, base images pulled
[ ] Venue wifi tested
[ ] Rehearsed 3×
[ ] Backup video recorded
[ ] Terminal font large, theme high-contrast
```

---

## Step 7.6 — Licensing

**Apache 2.0.** Reasons:

- Checkov is Apache 2.0; you invoke it as a subprocess, so you are not bound by it, but
  matching the ecosystem removes friction.
- Apache 2.0 includes an explicit patent grant. MIT does not. For a devops/infra tool
  that companies might adopt, that grant materially reduces legal review friction.
- It stays compatible with a future paid tier: Apache 2.0 for what's in this repo — the
  full self-hosted Docker Compose application — and a separate offering (managed
  hosting, team features, SSO) built on top later, licensed however makes sense at that
  point.

Do **not** choose AGPL for the core. It would block adoption at exactly the companies you
want as future customers.

---

## Step 7.7 — After the bootcamp: the first four things

Ordered by impact per hour, not by what's most fun:

1. **GitHub Actions workflow generation** (~1 day). You already have the analysis and the
   Dockerfile. Emitting a build-and-push workflow is templating. It is also the single
   most-requested artifact type after the Dockerfile, and it turns DeployMint from a
   deploy-it-yourself tool into something with a place in a team's actual CI pipeline.

2. **`deploymint export`** (~2 hours). Write the generated artifacts directly into the
   user's project directory (not just `.deploymint/{run_id}/`) as a PR-ready diff they
   can commit. This is the highest ratio of value to effort in the entire backlog.

3. **Connect a cloud account directly** (~3-4 days). Right now Kubernetes deploys go
   through whatever `~/.kube/config` is mounted — good for someone who already has a
   cluster, but a real barrier for someone who doesn't. Letting a user paste cloud
   credentials (scoped narrowly) so DeployMint can provision or reach a managed cluster
   is the natural next step, and it's exactly the gap flagged in the README's scope
   table.

4. **Live AWS Cost Explorer** (~1 day). Because you built against the real CE response
   shape, this is a source swap plus credential handling.

Deliberately **not** on this list: LoRA fine-tuning, multi-agent consensus voting, and a
plugin marketplace. All three are interesting, none moves adoption in the next quarter.

---

## Time budget

| Task | Hours |
|---|---|
| Image build + clean-env testing + package-data fixes | 2.0 |
| README + GIF recording | 2.5 |
| Example repos under `projects/` | 1.5 |
| Demo script + 3 rehearsals + backup video | 2.5 |
| Final checklist sweep | 1.5 |
| **Total** | **~10 h (1 day)** |
