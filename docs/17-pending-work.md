# 17 — Pending Work: Gaps Found After Phase 7

Phases 1–7 are complete and committed (see the README's progress tracker). This doc
tracks what's left, found by actually cross-checking the running app against
`PROJECT.md`'s original proposal rather than re-reading the phase docs. Two categories:
data the pipeline already computes but throws away before it reaches the user, and
real proposal features never built. Terraform/Ansible/ArgoCD/GitHub Actions generation
and Prometheus/Grafana manifests are explicitly OUT of scope — decided against, not
forgotten.

---

## Tier 1 — surface data the pipeline already computes

None of these need new agents or new architecture. Each one is data that exists in
memory during a run and is silently dropped before it reaches the DB or the UI.

### 17.1 — `reasoning` field discarded

`GeneratedArtifacts.reasoning` (`schemas/artifacts.py`) is populated by every LLM
generation call — Claude's own 2-3 sentence explanation of why it made the choices it
did (`prompts.py`'s `SMITH_SYSTEM` schema requires it). `smith.py`'s `run()` builds the
persisted `artifacts` dict from the `GeneratedArtifacts` object but never copies
`.reasoning` into it. It's generated on every real LLM call and never seen again.

**Fix:**
- `state.py`: add `reasoning: NotRequired[str]` to the `Artifacts` TypedDict
- `smith.py`: add `"reasoning": artifacts.reasoning` to the persisted dict (empty
  string on the template path, where there's nothing to reason about)
- `run.html` / `app.js`: show it under the Artifacts panel

### 17.2 — `critical_files` never surfaced

The Architect computes a PageRank-ranked list of the most-depended-on files
(`graph_builder.rank_criticality`) — this is one of the proposal's headline claims
("`db.py` is most critical — two modules depend on it") and it's stored in
`analysis.critical_files`, but nothing in the UI shows it or highlights those nodes on
the Cytoscape graph.

**Fix:**
- `project.html`: list `analysis.critical_files` next to the graph
- `app.js`'s `renderDepGraph()`: style nodes whose id is in `critical_files` distinctly
  (e.g. a border or fill color) so the graph visually answers "what's critical" instead
  of just "what imports what"

### 17.3 — Finding `explanation` never generated

`docs/07-phase-3-security.md` §4.3b specifies an LLM one-liner explaining the concrete
risk of each `critical`/`high` finding in plain language (`FINDING_EXPLANATION_PROMPT`
exists in `prompts.py`). `warden.py` never calls it — `Finding.explanation` is defined
in the schema and always empty.

**Fix:**
- `warden.py`: after computing the verdict, fan out one short LLM call per
  critical/high finding (capped — same reasoning as the Red Team severity cap: never
  gates `passed`, a failure here is silently skipped, not surfaced as an error)
- `run.html`: show `finding.explanation` under each finding when present

### 17.4 — Artifacts are bare download links

The run page's Artifacts panel is four `<a>` tags to raw text — no inline preview, no
syntax highlighting, despite `docs/10-phase-6-finops-ui.md`'s own mockup describing
"artifacts with syntax highlighting."

**Fix:** an inline `<pre>` tab-panel per file (Dockerfile / .dockerignore /
k8s-deployment.yaml / k8s-service.yaml), fetched from the existing
`/api/runs/{id}/artifacts/{filename}` endpoint, with a minimal client-side highlighter
(a small hand-rolled regex highlighter is enough here — vendoring a full highlight.js
for four file types is disproportionate, and the "no CDN" rule from Phase 6 still
applies).

---

## Tier 2 — real proposal features, not built

### 17.5 — Live AWS Cost Explorer

Today: `api/costs.py` always reads the bundled sample JSON export
(`data/sample_cost_export.json`) plus local per-run estimates. The proposal calls for a
live `boto3` Cost Explorer connection using the user's own IAM credentials — flagged in
the README as "planned" from day one, since the sample data was deliberately shaped
like the real API response so this would be a source swap, not a rewrite
(`docs/10-phase-6-finops-ui.md` §6.3).

**Design:**
- New optional env vars: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
  `AWS_REGION` (standard boto3 names, no new convention)
- `core/aws_cost.py`: calls `ce.get_cost_and_usage` grouped by service, same shape as
  the sample JSON, so `load_cost_data()` in `api/costs.py` only needs a source switch:
  live if credentials are present, sample JSON otherwise (mirrors the LLM
  resilience pattern — always degrade to a working state, never hard-fail)
- `/api/doctor`: new check reporting which cost source is active
- boto3 stays an optional dependency (`pyproject.toml`'s existing `[aws]` extra) —
  installing it is what turns the source on, no separate feature flag needed

### 17.6 — Model router (no more hardcoded Claude)

Today: every LLM call goes through `core/llm.py`'s `complete()`, which constructs an
`anthropic.Anthropic` client directly. There is no provider abstraction anywhere —
"swap to local LLaMA/DeepSeek" from the proposal is not just unbuilt, the seam for it
doesn't exist yet.

**Design — the smallest change that creates a real seam without a rewrite:**
- `core/llm.py` becomes a thin dispatcher: `complete()`/`complete_json()` keep their
  exact signatures (every call site — Smith, Red Team, Chat, Oracle — stays unchanged),
  but internally route through a `Provider` protocol (`complete_raw(system, user,
  **kw) -> str`)
- `core/providers/anthropic_provider.py`: today's implementation, moved as-is
- `core/providers/openai_compatible_provider.py`: for local runtimes that speak the
  OpenAI chat-completions shape (Ollama, vLLM, LM Studio all do) — covers "local
  LLaMA/DeepSeek" without writing one adapter per backend
- `Settings.llm_provider: Literal["anthropic", "openai_compatible"] = "anthropic"` +
  `Settings.llm_base_url` (for the local case) — one new setting, not a new
  orchestration layer
- `/api/doctor`'s existing `llm` check reports which provider is active

**Explicitly not doing:** LiteLLM itself as a dependency. It's a much larger surface
(100+ providers, its own config format) for a need that's actually just two shapes
(Anthropic's own SDK, and the OpenAI-compatible shape everything else converged on).
Matches the "smallest real seam" principle rather than pulling in a proxy layer.

---

## Explicitly out of scope (confirmed with the user, not an oversight)

- Terraform / Ansible / ArgoCD / GitHub Actions artifact generation
- Prometheus / Grafana manifest generation
- ChromaDB / vector-DB RAG (the static `fewshot.jsonl` is doing the job)
- tmux.ai multi-turn memory across CLI sessions

---

## Build order

1. Tier 1 (17.1–17.4) — cheap, no architecture change, immediately visible
2. 17.5 Live Cost Explorer — medium, additive, degrades safely
3. 17.6 Model router — largest, touches every LLM call site (safely, since the
   public signature doesn't change) — done last so Tier 1/2 aren't blocked on it
