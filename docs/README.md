# DeployMint — Documentation & Build Plan

This folder is the **single source of truth** for building DeployMint. Every document
here is written so you can follow it top-to-bottom without guessing.

> **Product shape (locked 2026-08-12):** a self-hosted app you run with Docker Compose.
>
> ```bash
> docker compose up -d
> ```
>
> That's it. Opens `http://localhost:8000` — a web dashboard, exactly like `mlflow ui`.
> Postgres is bundled in the same compose stack; the user never installs or configures a
> database. The LLM is a real hosted model (Claude), called over the internet from inside
> the container — that's the only thing that's "online." The app itself, the user's code,
> and everything it builds stay on the machine running the container.
>
> Full reasoning for this shape, and the two prior directions it replaced, is in
> [16-decisions-log.md](16-decisions-log.md).

---

## How to use these docs

1. Read `00-prerequisites.md` — it's short now. End users need Docker and Compose; that's
   the whole list. A separate section covers what *you* need to build the image.
2. Read `01-architecture.md` once, fully. It contains the decisions that are expensive to
   reverse later (the container topology, the database, the LLM boundary).
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
| 00 | [00-prerequisites.md](00-prerequisites.md) | End-user prerequisites (Docker + Compose) vs. what you need to build the image |
| 01 | [01-architecture.md](01-architecture.md) | Container topology, request flow, the locked decisions |
| 02 | [02-repo-layout.md](02-repo-layout.md) | Complete file tree — Dockerfile, compose file, app package, thin CLI |
| 03 | [03-data-model.md](03-data-model.md) | Postgres schema, Pydantic contracts, full REST API surface |
| 04 | [04-agents-spec.md](04-agents-spec.md) | All 7 agents: inputs, outputs, algorithms, prompts — LLM used wherever it helps |
| 05 | [05-phase-1-foundation.md](05-phase-1-foundation.md) | **Days 1–2** — package, server, DB, Architect Agent |
| 06 | [06-phase-2-generation.md](06-phase-2-generation.md) | **Days 3–4** — Claude-backed Artifact Smith, schema validation |
| 07 | [07-phase-3-security.md](07-phase-3-security.md) | **Days 5–7** — Checkov, OPA Rego, Red Team Agent |
| 08 | [08-phase-4-execution.md](08-phase-4-execution.md) | **Days 8–9** — Docker-outside-of-Docker builds, deploy, audit log |
| 09 | [09-phase-5-orchestration.md](09-phase-5-orchestration.md) | **Days 10–11** — LangGraph wiring, WebSocket streaming, the thin CLI client |
| 10 | [10-phase-6-finops-ui.md](10-phase-6-finops-ui.md) | **Days 12–13** — FinOps Agent, Observability Oracle, the web dashboard |
| 11 | [11-phase-7-polish-demo.md](11-phase-7-polish-demo.md) | **Day 14** — image build, compose bundle, demo script |
| 12 | [12-testing-strategy.md](12-testing-strategy.md) | Test pyramid, fixtures, sample repos, CI |
| 13 | [13-risks-and-cutlines.md](13-risks-and-cutlines.md) | What kills this project, and what to cut when behind |
| 14 | [14-command-reference.md](14-command-reference.md) | Every `docker compose` / CLI / API command, one page |
| 15 | [15-learning-path.md](15-learning-path.md) | What to learn, in what order, with time budgets |
| 16 | [16-decisions-log.md](16-decisions-log.md) | The full round trip — every direction this project considered and why it landed here |

---

## Progress tracker

Update this as you finish each phase. `[x]` = acceptance test passed.

```
[x] Phase 0 — Prerequisites green  ✅ 2026-08-12
      Python 3.11.15 · Docker 29.2.0 · kubectl · kind 0.32.0 (dev cluster)
      tmux 3.7b · OPA 1.19.0 (Rego v1) · checkov 3.3.10 · git initialized
[ ] Phase 1 — Foundation      (Days 1-2)  → app container boots, Architect returns real graph
[ ] Phase 2 — Generation      (Days 3-4)  → Claude writes valid Dockerfile + K8s manifest
[ ] Phase 3 — Security        (Days 5-7)  → Checkov + 3 Rego rules + Red Team block
[ ] Phase 4 — Execution       (Days 8-9)  → image builds via mounted docker.sock, pod runs
[ ] Phase 5 — Orchestration   (Days 10-11)→ LangGraph end-to-end, thin CLI streams live
[ ] Phase 6 — FinOps + UI     (Days 12-13)→ cost query answers, dashboard renders
[ ] Phase 7 — Polish + Demo   (Day 14)    → `docker compose up` on a clean machine works
```

---

## The one-sentence definition of done

> On a clean machine with only Docker and Compose installed: `docker compose up -d`,
> open `http://localhost:8000`, point it at a project mounted under `./projects`, click
> **Deploy**, and watch a dependency graph → generated artifacts → security verdict →
> live terminal → **running pod**, then ask *"which service costs the most?"* and get an
> answer.

If a feature does not serve that sentence, it is a stretch goal. See
`13-risks-and-cutlines.md`.

---

## Ground rules (learned the hard way, encoded here)

1. **Agents are plain Python classes first.** LangGraph is glue, added in Phase 5.
   Never write a LangGraph node before the function inside it works standalone.
2. **The state schema is frozen in Phase 1.** Changing it on Day 8 is a full-day refactor.
   It lives in `deploymint/agents/state.py` and nowhere else.
3. **Every LLM output is validated before it touches the disk.** No exceptions.
   LLMs will hand you markdown fences, prose apologies, and half-YAML.
4. **Template fallback for every generator.** If the LLM API is rate-limited, down, or
   refuses, the deterministic template runs. This is about reliability, not offline
   support — the product is always online — but a transient API blip must never break
   a customer's deploy.
5. **Sandbox every path.** The app only ever reads under the mounted projects volume.
   Resolved and prefix-checked. See `01-architecture.md` §1.7.
6. **MVP scope is Dockerfile + K8s Deployment/Service only.** Terraform, Ansible, ArgoCD,
   GitHub Actions are Phase-8+. The proposal lists 5 DSLs; you ship 2 artifacts.
7. **Use the LLM freely.** It's a hosted model called over the internet from inside a
   container you control — cost and latency are real constraints, cleverness to avoid
   using it is not. See `04-agents-spec.md` §4.9 for where it's used beyond artifact
   generation.
