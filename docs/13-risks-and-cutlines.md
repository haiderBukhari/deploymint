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

### 3. Local LLM quality (certain, moderate)

**The trap:** `llama3.1:8b` is not GPT-4o. It will produce invalid YAML, hallucinate
Dockerfile instructions, ignore parts of your prompt, and occasionally return an
apology instead of JSON.

**The reality:** this is fully mitigated by architecture, and the mitigation is already
in the plan — Pydantic validation → one repair attempt → deterministic template fallback.
Budget 2 hours of prompt iteration in Phase 2, then stop tuning.

**Guard:** if you have spent more than 3 hours on prompt engineering, your templates are
carrying the product and that is fine. Move on.

**Escape hatch:** if quality is blocking the demo, add an OpenAI provider branch in
`llm.complete()` (30 minutes, using the seam you already built) and use GPT-4o for the
demo while keeping Ollama as the documented default. Be transparent about which you used.

### 4. The Kubernetes deploy loop (certain, moderate)

**The trap:** the last mile is where local K8s bites — `ErrImagePull` because kind can't
see your Docker images, `CrashLoopBackOff` because the app writes to a read-only
filesystem, readiness probes pointing at an endpoint that doesn't exist.

**The reality:** each is a known 20–40 minute problem, and they are all listed with fixes
in `08-phase-4-execution.md` §4.1. The manual verification step exists specifically to
front-load this pain.

**Guard:** do the manual deploy on Day 1 of Phase 4, before writing any Python. If
`docker build → kind load → kubectl apply → curl /health` doesn't work by hand, no amount
of code will make it work automatically.

### 5. Building the UI too early (moderate, moderate)

**The trap:** a dashboard is visible, satisfying progress. It is also the most easily
cut deliverable, and every hour spent on CSS in week one is an hour not spent on the
deploy loop.

**The reality:** the CLI *is* a complete product. `deploymint up ./repo` with Rich
formatting demos beautifully. The web UI is Phase 6 for a reason.

**Guard:** no HTML before Phase 6. None.

---

## 13.2 Cutlines — what to drop, in order

You will fall behind. That is expected, not a failure. Cut from the **bottom** of this
list. Everything above a cut stays.

```
╔═════════════════════════════════════════════════════════════════╗
║  MUST SHIP — without these there is no product                  ║
╠═════════════════════════════════════════════════════════════════╣
║  1. deploymint server boots; doctor is green                    ║
║  2. Architect: language + framework + dependency graph          ║
║  3. Smith: Dockerfile + K8s manifests (LLM w/ template fallback)║
║  4. Warden: Checkov + 3 OPA rules, blocking verdict             ║
║  5. Execution: docker build → kind load → apply → running pod   ║
║  6. CLI `deploymint up` with live progress                      ║
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
║ 20. LiteLLM provider swap                                       ║
╚═════════════════════════════════════════════════════════════════╝
```

### The minimum viable demo

If everything goes wrong and you have one day left, this is what you build:

```bash
deploymint up ./examples/fastapi-app
```

→ shows detected stack → shows generated Dockerfile → shows security PASS →
builds the image → deploys → `curl /health` returns ok.

Then:

```bash
deploymint up ./examples/poisoned-repo
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

`export` is genuinely underrated. Right now artifacts live in `~/.deploymint/artifacts/`,
which is correct for safety but means the user can't actually *keep* them. Two hours turns
DeployMint from a demo into something someone uses twice.

---

## 13.4 Technical risks and their mitigations

| Risk | Probability | Mitigation | Already in the plan? |
|---|---|---|---|
| Checkov's stale pins conflict with networkx/importlib-metadata | **occurred** | verified benign — Checkov works on networkx 3.6.1; `pipx` escape hatch exists because Checkov is a subprocess, not an import | ✅ decision #9, §0.7 |
| A later `pip install` downgrades networkx to satisfy Checkov's pin | medium | `requirements.lock.txt` + a doctor check asserting `networkx>=3.3` | ✅ §0.6, §0.7 |
| OPA Rego v0/v1 syntax mismatch | high | version check in doctor; pick one dialect | ✅ §3.1 |
| tree-sitter grammar compilation fails | medium | `tree-sitter-language-pack` prebuilt binaries | ✅ decision #8 |
| Ollama too slow for the demo | medium | warm the model; `llama3.2` for dev | ✅ §2.1 |
| `database is locked` under concurrency | medium | WAL + `busy_timeout=5000` | ✅ §3.2 |
| kind can't see locally built images | high | `kind load` + `imagePullPolicy: IfNotPresent` | ✅ §0.3, §2.6 |
| Package data missing from the wheel | high | explicit `package-data` + CI verification | ✅ §7.1, §12.6 |
| WebSocket floods on docker build | medium | 100 ms batching | ✅ §5.3 |
| LLM emits `image: app:latest` | high | deterministic `_inject_image` post-processing | ✅ §2.6 |
| Blocking calls freeze the server | high | `asyncio.to_thread` for all sync SDK calls | ✅ §1.6 |
| Rollback fails on first deploy | high | delete-deployment fallback path | ✅ §6.2 |
| Two browser tabs split the event stream | medium | per-client subscriber queues | ✅ §5.3 |

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

Every "say instead" is **more** impressive than the overclaim, because it is specific and
verifiable. A reviewer who probes a precise claim finds substance. One who probes an
inflated claim finds the gap — and then doubts everything else.

---

## 13.6 Time reality check

| Phase | Planned | Realistic if things go wrong |
|---|---|---|
| 1 Foundation | 18 h | 22 h (tree-sitter fights you) |
| 2 Generation | 18 h | 24 h (prompt iteration) |
| 3 Security | 21 h | 26 h (Rego syntax) |
| 4 Execution | 20 h | 28 h (**the deploy loop**) |
| 5 Orchestration | 18 h | 22 h |
| 6 FinOps + UI | 20 h | 26 h |
| 7 Polish | 10 h | 12 h |
| **Total** | **125 h** | **160 h** |

125 hours across 14 days is **~9 hours/day, every day**. That is a real full-time
sprint with no slack.

**Therefore: plan to cut.** Look at the cutline list and pre-decide that items 14–20 are
out unless you are ahead. Deciding this on Day 1, calmly, is far better than deciding it
on Day 13 in a panic.

A realistic strong outcome: items **1–13 shipped**, items 14–20 documented as
"architecture supports it, not yet built." That is a genuinely impressive two weeks.
