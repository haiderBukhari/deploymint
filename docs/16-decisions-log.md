# 16 — Decisions Log

This project's architecture went through three distinct shapes before landing on the one
the other 15 docs describe. Recording the full round trip — not just the final answer —
because the reasoning for *rejecting* each prior shape is often more useful to a future
reader than the final decision alone.

---

## Shape 1 (original) — pip-installed, fully local

`pip install deploymint && deploymint server`. SQLite for storage. Ollama
(`llama3.1:8b`) as the default LLM, fully offline-capable. No cloud dependency at all.

**Why it was reasonable:** exactly MLflow's model, and the original project proposal's
"air-gapped, own your infra" positioning fit it well.

**Why it was replaced:** two problems surfaced once the actual usage model was discussed.
First, `pip install` puts the entire application source in a world-readable
`site-packages` directory — for a product whose IP is its prompts and agent logic, that's
distributing the source, not a built product. Second, a small local model's output
quality created real engineering overhead (a repair loop, heavy template dependence,
constant prompt-iteration budget) that a hosted model doesn't need.

---

## Shape 2 (rejected outright) — hosted multi-tenant SaaS

Considered next: the user's source code uploads to a server *we* operate, analysis and
builds happen on our infrastructure, the user never runs anything locally at all.

**Why it was rejected:** explicitly, and immediately, by the person building this. "No
one wants their code to run on our server" was the exact objection. It also would have
required solving multi-tenant isolation, a billing relationship, and a much larger trust
surface before the product had proven itself at all — scope inappropriate for an MVP.

---

## Shape 3 (also considered, also rejected) — Postgres + auto-provisioned local container

A brief detour: keep the pip-install/local-first shape, but swap SQLite for an
auto-provisioned Postgres container the app starts on first run, and swap Ollama for
Claude with a cloud-proxy auth seam. This produced a working set of docs
(`03-data-model.md`'s async-SQLAlchemy patterns partially descend from it) before being
reverted — it solved a problem nobody had actually asked to solve (Postgres before the
distribution model was settled) and got the distribution question backwards: it kept
`pip install` as the product's install mechanism, which was Shape 1's actual flaw.

**What survived from this detour:** the instinct that Postgres is the right database
long-term, and that Claude with structured outputs is a better default than Ollama. Both
turned out to be correct — they just needed the right distribution model wrapped around
them, which is Shape 4.

---

## Shape 4 (final) — Docker Compose, local execution, hosted-quality LLM

```bash
docker compose up -d
```

One command starts two containers: the DeployMint app and a bundled Postgres. The whole
application — FastAPI, all seven agents, Checkov, OPA, tree-sitter — ships as a **built
Docker image**, not as pip-installable source. The user's code is bind-mounted into the
already-running container; it never leaves their machine. Docker builds happen via a
mounted host socket (Docker-outside-of-Docker); Kubernetes deploys go through a mounted
kubeconfig if one exists, or fall back to plain `docker run` if it doesn't. The LLM is
Claude, called over the internet from inside the container — the only thing about this
architecture that is "online."

**Why this is the answer, reconciling both prior rejections:**

| Shape 1's problem | How Shape 4 fixes it |
|---|---|
| Source distributed as readable `site-packages` | Ships as a built image; the product is something you run, not source you're handed |
| Small local model, heavy engineering overhead | Claude, with a much smaller template-fallback surface |
| Cannot swap distribution model later without a rewrite | Postgres from day one means the eventual hosted tier is a `DATABASE_URL` change, not a migration |

| Shape 2's problem | How Shape 4 fixes it |
|---|---|
| User's code runs on infrastructure they don't control | Execution happens in a container running *on their own machine* |
| Multi-tenant isolation, billing, trust surface all needed solving immediately | None of that exists — it's a single-tenant, single-machine app, same trust model as any self-hosted tool |
| "No one wants their code to run on our server" | Nothing runs on "our" server at all in this shape |

**The one thing this shape requires that neither prior one did:** Docker-outside-of-
Docker, and the host-path translation it implies (`08-phase-4-execution.md` §4.1a). This
is real, genuinely new complexity — not free — but it's a well-understood pattern (every
CI system that builds Docker images uses it) and it's the cost of getting both "zero
setup" and "your code never leaves your machine" at the same time. Section 13.1 of
`13-risks-and-cutlines.md` treats it as the top technical risk for exactly this reason.

---

## What to take from this if the architecture needs to move again

The two axes that actually mattered, every time this was reconsidered:

1. **Where does the user's source code physically execute?** Their machine, or ours.
   This is the one that cannot be fudged — it determines the entire trust story.
2. **How is the product installed?** As readable source (`pip install`), or as a built
   artifact (a Docker image). This determines whether the product's own IP is exposed by
   the act of installing it.

Every prior shape got exactly one of these two axes wrong. Shape 4 is the one point in
that 2×2 that satisfies both — local execution, built-artifact distribution — and that is
*why* it's final, not just that it happened to be the last one discussed.

If a future revision considers a hosted tier (the roadmap in `11-phase-7-polish-demo.md`
§7.7 gestures at this), the honest framing is: that is **Shape 2, deliberately re-added
as an opt-in**, not a replacement for Shape 4. The local Docker Compose product should
keep working exactly as documented here regardless of whether a hosted tier ever exists
alongside it.
