# 13 — Risks & Cutlines

## 13.1 The five things most likely to kill this project

Ranked by probability × damage.

### 1. Over-engineering the orchestration (very likely, fatal)

**The trap:** DeployMint sounds like "a society of agents." That framing invites
consensus voting, agent-to-agent negotiation, retry policies, memory sharing, and a
supervisor agent. Each is interesting. Together they consume your entire two weeks and
produce nothing demoable.

**The reality:** your MVP graph is a **linear chain with one conditional edge**. Seven
nodes, one branch at the security gate. That is it. LangGraph earns its place because it
gives you streaming, typed state, and clean node boundaries — not because you need
multi-agent negotiation.

**Guard:** if you find yourself writing an agent that talks to another agent, stop. In
this architecture, agents communicate only through `DeployState`.

### 2. Chasing all five DSLs (likely, severe)

**The trap:** the proposal promises Dockerfile, Kubernetes, Terraform, Ansible, ArgoCD,
and GitHub Actions. Six artifact types × validation × security policies × templates ×
few-shot examples for each ≈ three weeks of work on its own.

**The reality:** ship **Dockerfile + K8s Deployment + Service**. Three files, done
excellently, that actually build and actually run. Then say: "Terraform and Ansible are
the same pipeline with different templates — the architecture is already there."

That sentence is more convincing than four half-broken generators.

**Guard:** do not start a second artifact family until the first one deploys a running
pod end-to-end.

### 3. Docker-outside-of-Docker path translation (certain, moderate)

**The trap:** this project's earlier draft worried about *local LLM output quality*
(an 8B model producing invalid YAML). That risk is gone — Claude with structured
validation clears it easily (`04-agents-spec.md` §4.2, §4.10). The risk that replaced it
is more mundane and more certain to actually bite you: the app runs *inside* a container
but must build images on the *host's* Docker daemon via a mounted socket
(`08-phase-4-execution.md` §4.1a), which means every build context path has to be
translated from the container's `/workspace` view to the host's real filesystem path —
get this wrong and you get a confusing "no such file or directory" for a file that
demonstrably exists.

**The reality:** this is fully mitigated by architecture — `docker_engine.to_host_path()`
is one small function, tested once, used everywhere a path crosses that boundary. Budget
1.5 hours to get it right and verified in Phase 4, not less.

**Guard:** if a build fails with a path-not-found error and the file is clearly there
when you `docker compose exec app ls` it, check `DEPLOYMINT_PROJECTS_DIR_HOST` first,
before anything else.

### 4. The deploy loop — now with two paths to get right (certain, moderate)

**The trap:** the last mile is where this bites — `ErrImagePull` because a kind cluster
can't see your Docker images, `CrashLoopBackOff` because the app writes to a read-only
filesystem, readiness probes pointing at an endpoint that doesn't exist. This project now
has **two** deploy paths to verify — Kubernetes (if a cluster is reachable) and plain
`docker run` (if not, `01-architecture.md` §1.4 decision 12) — and it is tempting to only
test the one you personally have set up.

**The reality:** each Kubernetes failure mode is a known 20–40 minute problem, listed
with fixes in `08-phase-4-execution.md` §4.1b. The docker-run path is new and simpler,
but untested it is just as capable of silently never being exercised until a real user
without a cluster hits it on day one.

**Guard:** do the manual deploy on Day 1 of Phase 4 for **both** paths — comment out the
kubeconfig mount and confirm `docker run` still reaches a healthy container before
writing any Python. If either doesn't work by hand, no amount of code will make it work
automatically.

### 5. Building the UI too early (moderate, moderate)

**The trap:** a dashboard is visible, satisfying progress. It is also the most easily
cut deliverable, and every hour spent on CSS in week one is an hour not spent on the
deploy loop.

**The reality:** the thin CLI *is* a complete product on its own — `deploymint up
./projects/my-app` with Rich formatting demos beautifully, and it's genuinely minimal to
build (`09-phase-5-orchestration.md` §5.5) since it's a pure HTTP+WS client with no agent
logic in it. The web dashboard is still the *primary* interface per
`01-architecture.md` §1.4 decision 13, but it's Phase 6 for a reason — order matters more
than which one wins in the end.

**Guard:** no HTML before Phase 6. None.

---

## 13.2 Cutlines — what to drop, in order

You will fall behind. That is expected, not a failure. Cut from the **bottom** of this
list. Everything above a cut stays.

```
╔═════════════════════════════════════════════════════════════════╗
║  MUST SHIP — without these there is no product                  ║
╠═════════════════════════════════════════════════════════════════╣
║  1. App container boots via `docker compose up -d`; /api/doctor green ║
║  2. Architect: language + framework + dependency graph          ║
║  3. Smith: Dockerfile + K8s manifests (Claude w/ template fallback)║
║  4. Warden: Checkov + 3 OPA rules, blocking verdict             ║
║  5. Execution: docker build (via mounted socket) → apply/run → healthy ║
║  6. Thin CLI `deploymint up` with live progress                 ║
║  7. Recorded session + audit log                                ║
╠═════════════════════════════════════════════════════════════════╣
║  SHOULD SHIP — these are what make it a *product*               ║
╠═════════════════════════════════════════════════════════════════╣
║  8. Red Team deterministic probes                               ║
║  9. Poisoned-repo block demo                                    ║
║ 10. LangGraph orchestration (vs. the linear driver)             ║
║ 11. WebSocket live streaming                                    ║
║ 12. FinOps cost estimate + one NL query                         ║
║ 13. Web UI — run page only                                      ║
╠═════════════════════════════════════════════════════════════════╣
║  NICE TO HAVE — cut these first, without hesitation             ║
╠═════════════════════════════════════════════════════════════════╣
║ 14. Red Team LLM layer          ← cut #1                        ║
║ 15. Observability Oracle + rollback                             ║
║ 16. Web UI — project list, graph viz, cost page                 ║
║ 17. Multi-turn chat memory                                      ║
║ 18. JS / Go / Java import parsing (Python only is fine)         ║
║ 19. Audit hash chain (plain logs still demo well)               ║
║ 20. Packaging the thin CLI as its own pip-installable package   ║
║     (docker compose exec app ... works fine without it)         ║
╚═════════════════════════════════════════════════════════════════╝
```

### The minimum viable demo

If everything goes wrong and you have one day left, this is what you build:

```bash
deploymint up ./projects/fastapi-app
```

→ shows detected stack → shows generated Dockerfile → shows security PASS →
builds the image → deploys (Kubernetes or plain `docker run`, whichever is reachable) →
`curl /health` returns ok.

Then:

```bash
deploymint up ./projects/poisoned-repo
```

→ **BLOCKED**, with the reason.

That is a complete, compelling story in two commands. Everything else is amplification.

---

## 13.3 Scope traps that look small but aren't

| "Just add…" | Actual cost | Verdict |
|---|---|---|
| "…Terraform generation" | 3 days (HCL validation, provider config, state) | ❌ post-MVP |
| "…Ansible playbooks" | 2 days (inventory, idempotency, no local test target) | ❌ post-MVP |
| "…real AWS deployment" | 2 days (IAM, VPC, ECR, EKS auth, and a bill) | ❌ post-MVP |
| "…ChromaDB RAG" | 1 day + dep conflicts, for marginal gain over 20 few-shot examples | ❌ cut |
| "…Prophet forecasting" | 4 h install + no time-series data to forecast | ❌ cut |
| "…LoRA fine-tuning" | 1 week + GPU + a dataset you don't have | ❌ not this quarter |
| "…multi-agent consensus" | 2 days for a linear pipeline that doesn't need voting | ❌ never |
| "…a plugin marketplace" | needs users first | ❌ post-launch |
| "…GitHub Actions generation" | 1 day, mostly templating | ⚠️ **best post-MVP item** |
| "…`deploymint export` to the repo" | 2 hours | ✅ **do it if you have a spare afternoon** |

`export` is genuinely underrated. Right now artifacts live in
`{project}/.deploymint/{run_id}/` — visible and inspectable, but not what most people
think of as "the actual Dockerfile for this project" until it's promoted out of that
folder. Two hours turns DeployMint from a demo into something someone uses twice.

---

## 13.4 Technical risks and their mitigations

| Risk | Probability | Mitigation | Already in the plan? |
|---|---|---|---|
| Checkov's stale pins conflict with networkx/importlib-metadata | **occurred** | verified benign — Checkov works on networkx 3.6.1; `pipx` escape hatch exists because Checkov is a subprocess, not an import | ✅ decision #10, §0.6 |
| A later `pip install` downgrades networkx to satisfy Checkov's pin | medium | `requirements.lock.txt` + a doctor check asserting `networkx>=3.3` | ✅ §0.6 |
| OPA Rego v0/v1 syntax mismatch | high | version check in doctor; pick one dialect | ✅ 07 §3.1 |
| tree-sitter grammar compilation fails | medium | `tree-sitter-language-pack` prebuilt binaries | ✅ decision #9 |
| Docker socket host-path translation bug (§13.1 risk #3) | high | one tested `to_host_path()` function, exercised on Day 1 of Phase 4 | ✅ 08 §4.1a |
| Anthropic API rate limits or latency spikes during the demo | medium | warm-up call in pre-flight; template fallback is always live, not a special mode | ✅ 06 §2.1, 11 §7.4 |
| Stale pooled Postgres connection after `db` container restarts | medium | `pool_pre_ping=True` on the engine | ✅ 03 §3.2 |
| kind can't see locally built images | high (only in kind-based dev/demo clusters) | `kind load` + `imagePullPolicy: IfNotPresent`; a real cloud cluster or Docker Desktop K8s doesn't need this step at all | ✅ 08 §4.1b |
| No cluster reachable at all (common for a first-time user) | high | `docker run` fallback path, not a failure mode | ✅ 08 §4.6, §4.7 |
| Package data missing from the built image | high | explicit `package-data` + CI image-build verification | ✅ 11 §7.1, 12 §12.6 |
| WebSocket floods on docker build | medium | 100 ms batching | ✅ 09 §5.3 |
| LLM emits `image: app:latest` | high | deterministic `_inject_image` post-processing | ✅ 06 §2.6 |
| Blocking calls freeze the server | high | `asyncio.to_thread` for all sync SDK calls | ✅ 01 §1.6 |
| Rollback fails on first deploy | high | delete-deployment fallback path | ✅ 10 §6.2 |
| Two browser tabs split the event stream | medium | per-client subscriber queues | ✅ 09 §5.3 |
| Mounted Docker socket is root-equivalent host access | **always true, not a bug** | documented plainly, bound to `127.0.0.1` by default | ✅ 01 §1.7 |

Every high-probability risk has a mitigation already written into a phase doc. That is
the point of planning this thoroughly — the surprises are the ones you didn't list.

---

## 13.5 Claims to make carefully

Being precise here protects you from the one question that can deflate a demo.

| Tempting claim | Problem | Say instead |
|---|---|---|
| "Cryptographically secure audit log" | no key, no external anchor; DB write access defeats it | "Hash-chained, tamper-**evident** audit log — I can show you it detecting a modification" |
| "Every command runs in a recorded tmux session" | commands execute via subprocess; tmux is a parallel recording surface | "Every command is recorded with full argv, output, and exit code, and you can attach to the live session" |
| "ML-powered anomaly detection" | 12 samples isn't statistically meaningful | "IsolationForest over the deployment window, plus deterministic crash/restart/OOM checks — the deterministic rules are what protect you today" |
| "Supports 5 infrastructure DSLs" | you ship 2 artifact types | "Dockerfile and Kubernetes today; the pipeline is template-driven, so Terraform and Ansible are additional templates, not new architecture" |
| "150–200 few-shot examples" | you'll have ~20 | "A curated few-shot set, retrieved by stack — 20 good examples outperform 200 mediocre ones at this context size" |
| "Understands your entire codebase" | file-level import graph, not semantic understanding | "Builds a real import graph with tree-sitter and ranks files by PageRank — it knows which modules are load-bearing" |
| "Optimizes your cloud spend" | it estimates and recommends; it doesn't act | "Estimates cost from your actual resource requests and flags over-provisioning" |
| "Runs entirely offline / local-first" | the LLM calls are real network requests to a hosted API — the product is online by design | "Your code, your build, and your cluster never leave this machine. The one thing that does is the request to generate a config — and even that has a deterministic fallback if it's unavailable" |
| "One-command install" | it's `git clone`, copy `.env`, add a key, then `docker compose up` | "Four commands, and the fourth is the only one that matters after the first time" |

Every "say instead" is **more** impressive than the overclaim, because it is specific and
verifiable. A reviewer who probes a precise claim finds substance. One who probes an
inflated claim finds the gap — and then doubts everything else.

---

## 13.6 Time reality check

| Phase | Planned | Realistic if things go wrong |
|---|---|---|
| 1 Foundation | 17.5 h | 21 h (tree-sitter fights you) |
| 2 Generation | 17 h | 21 h (prompt iteration is smaller now, but still real) |
| 3 Security | 21 h | 26 h (Rego syntax) |
| 4 Execution | 23.5 h | 30 h (**the deploy loop, now with two paths and a path-translation bug class**) |
| 5 Orchestration | 18 h | 22 h |
| 6 FinOps + UI | 19.5 h | 25 h |
| 7 Polish | 10 h | 12 h |
| **Total** | **~127 h** | **~157 h** |

127 hours across 14 days is **~9 hours/day, every day**. That is a real full-time
sprint with no slack. Phase 4 grew relative to the original plan — Docker-outside-of-
Docker and the `docker run` fallback are genuinely new surface area, not padding — while
Phase 2 shrank, because Claude's structured-output reliability removed most of the
prompt-iteration and repair-loop debugging an 8B local model demanded.

**Therefore: plan to cut.** Look at the cutline list and pre-decide that items 14–20 are
out unless you are ahead. Deciding this on Day 1, calmly, is far better than deciding it
on Day 13 in a panic.

A realistic strong outcome: items **1–13 shipped**, items 14–20 documented as
"architecture supports it, not yet built." That is a genuinely impressive two weeks.
