# 11 — Phase 7: Packaging, Docs & Demo (Day 14)

**Goal:** `pip install` from a built wheel works on a clean environment, the README makes
someone want to try it, and you can run a 5-minute demo without touching a keyboard
shortcut you haven't rehearsed.

---

## Step 7.1 — Build and test the wheel on a clean environment

```bash
pip install build && python -m build
```

```bash
ls -la dist/
```

Now the real test — a fresh venv that has never seen your source tree:

```bash
python3.11 -m venv /tmp/dmclean && /tmp/dmclean/bin/pip install dist/deploymint-0.1.0-py3-none-any.whl
```

```bash
cd /tmp && DEPLOYMINT_HOME=/tmp/dmhome /tmp/dmclean/bin/deploymint doctor
```

```bash
cd /tmp && DEPLOYMINT_HOME=/tmp/dmhome /tmp/dmclean/bin/deploymint server --port 8010
```

**What breaks here, every time:** package data. Your `.rego` policies, `fewshot.jsonl`,
`rate_card.json`, and `web/templates/` are not Python modules, so setuptools skips them
unless `[tool.setuptools.package-data]` lists them. Verify:

```bash
/tmp/dmclean/bin/python -c "
from importlib.resources import files
import deploymint
p = files('deploymint')
for rel in ['policies/no_root_user.rego','data/rate_card.json','data/fewshot.jsonl','web/templates/run.html','web/static/app.js']:
    print(('OK  ' if (p/rel).is_file() else 'MISS'), rel)
"
```

Every line must say `OK`. If any say `MISS`, fix `package-data` and rebuild.

**Also verify:** anywhere you used `Path(__file__).parent / "policies"` will work in a
source checkout and break subtly in some install layouts. Use `importlib.resources.files()`
everywhere. Grep for `__file__` and convert.

---

## Step 7.2 — README

The README is the product's storefront. Structure, in order:

1. **One line + a GIF.** The GIF is the single highest-value asset in the repo. Someone
   decides whether to try DeployMint in about four seconds.
2. **What it does** — 4 bullets, no adjectives.
3. **Install** — three commands, copy-pasteable.
4. **Quickstart** — the 60-second path to a running pod.
5. **How it works** — the agent pipeline diagram from `01-architecture.md`.
6. **What's real vs. what's next** — honest scope table.
7. **Configuration** — the env var table.
8. **Requirements** — Python 3.11+, Docker, kubectl, a cluster, Ollama.
9. **Contributing / License.**

### Recording the GIF

```bash
brew install asciinema agg
```

```bash
asciinema rec demo.cast --cols 100 --rows 30
```

Run `deploymint up ./examples/fastapi-app`, then exit and convert:

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
| Dockerfile + K8s Deployment/Service generation | ✅ working |
| Checkov + 3 custom OPA policies + adversarial probes | ✅ working |
| Recorded execution with hash-chained audit log | ✅ working |
| Local Kubernetes deploy (kind / Docker Desktop) | ✅ working |
| Cost estimation from manifests + NL cost queries | ✅ working |
| Terraform / Ansible / ArgoCD / GitHub Actions generation | 🚧 planned |
| Live AWS Cost Explorer connection | 🚧 planned (sample data today) |
| Managed cloud clusters (EKS/GKE/AKS) | 🚧 planned |
```

Listing what does *not* work yet is not a weakness. It is the difference between a
project people trust and one they bounce off after the first broken promise. Every strong
open-source README does this.

---

## Step 7.3 — Example repos

`examples/` — separate from `tests/fixtures/`, these are for users:

```
examples/
├── fastapi-app/     # the flagship — must deploy cleanly every time
├── express-app/
├── go-service/
└── README.md        # "pick one and run: deploymint up ./examples/fastapi-app"
```

Each must have a `/health` endpoint and deploy without a single manual fix.

---

## Step 7.4 — The demo script

`scripts/demo.sh`, but **know it by heart** — do not read from the script live.

### Pre-flight (run 10 minutes before, not during)

```bash
kind delete cluster --name deploymint; kind create cluster --name deploymint
```

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml && kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

```bash
rm -rf ~/.deploymint && ollama run llama3.1:8b "warm up" > /dev/null
```

```bash
docker pull python:3.11-slim
```

The last two matter enormously. **A cold Ollama takes 30+ seconds to load 4.9 GB into
memory.** A cold `python:3.11-slim` pull adds 20 seconds to your build. Both are pure
dead air in a demo. Warm them.

### The 5-minute run

**0:00 — The problem (30s).** No slides. Show a real repo.
> "This is a FastAPI service. To deploy it I need a Dockerfile, a Deployment, a Service,
> resource limits, a security context, and probes. That's 45 minutes of YAML I'll get
> subtly wrong. And I won't know it's wrong until it's running."

**0:30 — One command (15s).**
```bash
deploymint up ./examples/fastapi-app
```

**0:45 — Architect (30s).** Point at the terminal.
> "Tree-sitter parsed the AST, built an import graph, and ranked files by PageRank.
> `db.py` is most critical — two modules depend on it. No LLM involved; this is
> deterministic static analysis."

**1:15 — Smith (45s).** The Dockerfile appears.
> "Local Llama 3.1 on my machine — no code left this laptop. Note what it got right:
> multi-stage build, pinned base image, non-root UID 10001, dependency layer cached
> before source. That's specific to *this* repo, not a template."

**2:00 — Warden + Red Team (45s).**
> "Checkov's 550 rules plus three custom OPA policies I wrote. Passed. But watch this."

**2:45 — THE MOMENT (75s).** Switch to the poisoned repo.
```bash
deploymint up ./examples/poisoned-repo
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
sqlite3 ~/.deploymint/deploymint.db "UPDATE audit_logs SET output='nothing happened' WHERE seq=3"
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
> receipt, and tells you what it costs. `pip install deploymint`. Runs entirely on your
> machine."

### Demo rules

| Rule | Why |
|---|---|
| Terminal font ≥ 18pt | nobody can read 12pt on a projector |
| Rehearse the whole thing 3× | you will discover a broken step on run 2 |
| Record a backup video | live demos fail; a video is not a failure, it's a fallback |
| Never say "it should work" | if you're unsure, don't show it |
| Have the poisoned-repo output pre-captured | this is your best moment — protect it |
| Pin your `RUN_ID` in a shell var | typing a 12-char hex ID live is a guaranteed stumble |

---

## Step 7.5 — Final checklist

```
CODE
[ ] `ruff check deploymint` clean
[ ] `pytest -m "not slow"` green
[ ] `pytest` (including slow) green with a cluster up
[ ] No `print()` left in library code — Rich console or logging only
[ ] No hardcoded paths (grep for /Users/)
[ ] No API keys, tokens, or personal paths in the repo
[ ] `grep -rn "__file__" deploymint/` → all converted to importlib.resources

PACKAGING
[ ] Wheel builds without warnings
[ ] Clean-venv install works
[ ] All package data present after install (§7.1 check)
[ ] `deploymint --version` correct
[ ] `deploymint --help` readable and complete
[ ] Every command has a docstring that appears in --help

DOCS
[ ] README with GIF, install, quickstart, honest scope table
[ ] LICENSE (Apache 2.0)
[ ] examples/ with 3 working repos
[ ] CONTRIBUTING.md (even a short one)
[ ] The docs/ directory is in the repo — it shows your thinking

DEMO
[ ] Cluster fresh, metrics-server installed
[ ] Ollama warm, base images pulled
[ ] ~/.deploymint wiped
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
- It is compatible with the freemium→SaaS model in the proposal: Apache 2.0 for the CLI
  and server, and a separate commercial license for the team features (shared memory,
  centralized audit, SSO) later.

Do **not** choose AGPL for the core. It would block adoption at exactly the companies you
want as future customers. If you later want protection against a cloud provider hosting
your product, that is what a BSL or a dual-license on the *server/team* component is for
— not the CLI.

---

## Step 7.7 — After the bootcamp: the first four things

Ordered by impact per hour, not by what's most fun:

1. **GitHub Actions workflow generation** (~1 day). You already have the analysis and the
   Dockerfile. Emitting a build-and-push workflow is templating. It is also the single
   most-requested artifact type after the Dockerfile, and it turns DeployMint from a local
   tool into something with a place in a team's actual pipeline.

2. **`deploymint export`** (~2 hours). Write the generated artifacts into the user's repo
   as a PR-ready diff. Right now everything lives in `~/.deploymint/artifacts/`, which is
   great for safety and useless for adoption. This is the highest ratio of value to effort
   in the entire backlog.

3. **Terraform module generation** (~3 days). The proposal's biggest scope item. Only
   start it once #1 and #2 land — it is where the remaining DSLs get real.

4. **Live AWS Cost Explorer** (~1 day). Because you built against the real CE response
   shape, this is a source swap plus credential handling.

Deliberately **not** on this list: LoRA fine-tuning, multi-agent consensus voting, and a
plugin marketplace. All three are interesting, none moves adoption in the next quarter.

---

## Time budget

| Task | Hours |
|---|---|
| Wheel build + clean-env testing + package-data fixes | 2.0 |
| README + GIF recording | 2.5 |
| Example repos | 1.5 |
| Demo script + 3 rehearsals + backup video | 2.5 |
| Final checklist sweep | 1.5 |
| **Total** | **~10 h (1 day)** |
