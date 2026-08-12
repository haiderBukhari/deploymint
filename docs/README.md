# DeployMint — Documentation & Build Plan

This folder is the **single source of truth** for building DeployMint. Every document
here is written so you can follow it top-to-bottom without guessing.

> **Product shape (decided):** local-first, MLflow-style.
> `pip install deploymint` → `deploymint server` → everything runs on **your** machine.
> Local LLM (Ollama) by default. No cloud account required to get a green run.

---

## How to use these docs

1. Read `00-prerequisites.md` and get every checkbox green. **Do not skip this.**
2. Read `01-architecture.md` once, fully. It contains the decisions that are expensive
   to reverse later (state schema, DB, LLM boundary).
3. Then work **phase by phase**: `05` → `11`. Each phase doc has:
   - Goal (one sentence)
   - Files you will create (exact paths)
   - Full code
   - **Acceptance test** — a command you run; if it passes, the phase is done
4. Tick the tracker below as you go.

---

## Document index

| # | File | What it covers |
|---|---|---|
| 00 | [00-prerequisites.md](00-prerequisites.md) | Machine audit results, exact install commands, `deploymint doctor` spec |
| 01 | [01-architecture.md](01-architecture.md) | Local-first architecture, request flow, the 12 locked decisions |
| 02 | [02-repo-layout.md](02-repo-layout.md) | Complete file tree — purpose of every single file |
| 03 | [03-data-model.md](03-data-model.md) | SQLite schema, Pydantic contracts, full REST API surface |
| 04 | [04-agents-spec.md](04-agents-spec.md) | All 7 agents: inputs, outputs, algorithms, exact prompts |
| 05 | [05-phase-1-foundation.md](05-phase-1-foundation.md) | **Days 1–2** — package, CLI, server, DB, Architect Agent |
| 06 | [06-phase-2-generation.md](06-phase-2-generation.md) | **Days 3–4** — LLM layer, Artifact Smith, schema validation |
| 07 | [07-phase-3-security.md](07-phase-3-security.md) | **Days 5–7** — Checkov, OPA Rego, Red Team Agent |
| 08 | [08-phase-4-execution.md](08-phase-4-execution.md) | **Days 8–9** — tmux recording, Docker build, kind deploy, audit log |
| 09 | [09-phase-5-orchestration.md](09-phase-5-orchestration.md) | **Days 10–11** — LangGraph wiring, WebSocket streaming, tmux.ai NL router |
| 10 | [10-phase-6-finops-ui.md](10-phase-6-finops-ui.md) | **Days 12–13** — FinOps Agent, Observability Oracle, web UI |
| 11 | [11-phase-7-polish-demo.md](11-phase-7-polish-demo.md) | **Day 14** — packaging, README, demo script, recording |
| 12 | [12-testing-strategy.md](12-testing-strategy.md) | Test pyramid, fixtures, sample repos, CI |
| 13 | [13-risks-and-cutlines.md](13-risks-and-cutlines.md) | What kills this project, and what to cut when behind |
| 14 | [14-command-reference.md](14-command-reference.md) | Every CLI command + every API endpoint, one page |
| 15 | [15-learning-path.md](15-learning-path.md) | What to learn, in what order, with time budgets |

---

## Progress tracker

Update this as you finish each phase. `[x]` = acceptance test passed.

```
[x] Phase 0 — Prerequisites green  ✅ 2026-08-12
      Python 3.11.15 venv · Docker 29.2.0 · kubectl · kind 0.32.0
      cluster kind-deploymint Ready · Ollama 0.23.1 (llama3.1:8b, nomic-embed-text)
      tmux 3.7b · OPA 1.19.0 (Rego v1) · checkov 3.3.10 · git initialized
      165 packages locked in requirements.lock.txt
[ ] Phase 1 — Foundation      (Days 1-2)  → server boots, Architect returns real graph
[ ] Phase 2 — Generation      (Days 3-4)  → LLM writes valid Dockerfile + K8s manifest
[ ] Phase 3 — Security        (Days 5-7)  → Checkov + 3 Rego rules + Red Team block
[ ] Phase 4 — Execution       (Days 8-9)  → image builds, pod runs, session replayable
[ ] Phase 5 — Orchestration   (Days 10-11)→ LangGraph end-to-end, live stream to browser
[ ] Phase 6 — FinOps + UI     (Days 12-13)→ cost query answers, dashboard renders
[ ] Phase 7 — Polish + Demo   (Day 14)    → pip install from wheel works on clean venv
```

---

## The one-sentence definition of done

> On a clean machine: `pip install deploymint && deploymint server`, open
> `http://localhost:8000`, register a Python repo, click **Deploy**, and watch a
> dependency graph → generated artifacts → security verdict → live terminal →
> **running pod**, then ask *"which service costs the most?"* and get an answer.

If a feature does not serve that sentence, it is a stretch goal. See
`13-risks-and-cutlines.md`.

---

## Ground rules (learned the hard way, encoded here)

1. **Agents are plain Python classes first.** LangGraph is glue, added in Phase 5.
   Never write a LangGraph node before the function inside it works standalone.
2. **The state schema is frozen in Phase 1.** Changing it on Day 8 is a full-day refactor.
   It lives in `deploymint/agents/state.py` and nowhere else.
3. **Every LLM output is parsed by Pydantic before it touches the disk.** No exceptions.
   LLMs will hand you markdown fences, prose apologies, and half-YAML.
4. **Template fallback for every generator.** If the LLM is down or returns garbage,
   the deterministic template runs. The demo must never depend on a model behaving.
5. **Sandbox every path.** The server reads user repos. Only registered project roots,
   resolved and prefix-checked. See `01-architecture.md` §7.
6. **MVP scope is Dockerfile + K8s Deployment/Service only.** Terraform, Ansible, ArgoCD,
   GitHub Actions are Phase-8+. The proposal lists 5 DSLs; you ship 2 artifacts.
