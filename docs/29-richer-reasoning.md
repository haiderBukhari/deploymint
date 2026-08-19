# 29. Richer LLM reasoning

## What was wrong

Smith's `reasoning` field was reliably a 2-3 sentence throwaway. Three
independent causes stacked up:

1. **The prompt asked for almost nothing.** `prompts.py`'s schema block spelled
   out `"reasoning": "<2-3 sentences on the key choices you made>"` — the model
   did exactly what it was told.
2. **The token budget was shared and small.** `smith.py` passed no `max_tokens`
   to `llm.complete`, so the call inherited the default (4000 tokens) split
   across the Dockerfile, `.dockerignore`, both Kubernetes manifests, *and*
   the reasoning text. There was no room left for a real explanation even if
   one had been requested.
3. **The model couldn't see the thing it was supposed to reason about.**
   `smith.py`'s `TRIM_KEYS` never forwarded `graph`, `services`, or
   `dockerfile_exists` to the LLM, and capped `critical_files` at 5 with no
   context about *why* they were critical. Asking for "reference real files
   that influenced this decision" was asking the model to invent names it had
   never been shown.

Separately, `agents/architect.py` had a fully-written prompt
(`ARCHITECT_SUMMARY_PROMPT`) and a `analysis["architecture_summary"]` field
already rendered by `project.html` — but nothing ever called the prompt. The
Architect made zero LLM calls, so that block was permanently empty. Same root
cause, adjacent agent: computed data (`cycles`, from `find_cycles`) was also
being discarded into an error string instead of persisted on `analysis`.

## What changed

- **`state.py`**: added `reasoning_detail: NotRequired[str]` to `Artifacts` (a
  new key, not a widened `reasoning`, per this file's frozen-schema rule) and
  `cycles` / `architecture_summary` to `RepoAnalysis` (the latter already
  existed as a key; only the Architect's LLM call was missing).
- **`schemas/artifacts.py`**: `GeneratedArtifacts` gained `reasoning_detail: str = ""`.
- **`prompts.py`**: `SMITH_SYSTEM`'s schema block now asks for a structured,
  multi-paragraph `reasoning_detail` — referencing the import graph, critical
  files, and rejected alternatives, not generic Dockerfile advice.
  `SMITH_USER` gained an instruction to ground it in the *actual* dependency
  graph and file names supplied in the prompt, not generic advice.
- **`smith.py`**:
  - `TRIM_KEYS` widened; a new `_graph_summary()` helper sends node/edge
    counts plus only the edges touching critical files (bounded by
    `_MAX_GRAPH_EDGES = 40`) instead of the full raw graph — keeps the payload
    small while giving the model real file-level context.
  - `services` and `critical_files` (now 10, was 5) are included.
  - `SMITH_MAX_TOKENS = 8000` is now passed explicitly on both the initial
    call and the repair call, instead of inheriting the shared 4000 default.
  - The result dict now carries `reasoning_detail` alongside `reasoning`.
- **`architect.py`**: `cycles` is persisted on `analysis` (previously computed,
  then thrown away after building an error string). A new `_summarize()`
  method calls `ARCHITECT_SUMMARY_PROMPT` for the first time ever, wrapped in
  a bare `try/except -> ""` — the Architect's actual contract is the
  deterministic fields; the summary is a bonus caption that must never make
  this agent raise.
- **`run.html` / `project.html`**: render `reasoning_detail` as one `<p>` per
  `\n\n`-separated paragraph (not one giant blob), and render `cycles` as an
  explicit circular-import warning.
- **`style.css`**: `.reasoning-detail` styling to match the existing
  `.reasoning` block's tone.

## Verified

- `tests/test_smith.py`: 10/10 passing, including new tests asserting
  `reasoning_detail` passes through, the template-fallback path leaves it
  empty (never `None`), the LLM call now requests `max_tokens > 4000`, and the
  prompt actually contains `import_graph_summary` / `"services"` / a real file
  path from the graph (`app/db.py`) — proving `TRIM_KEYS` genuinely widened
  rather than just changing a constant.
- `tests/test_architect.py`: 8 tests (3 pre-existing + 5 new), covering
  `cycles` persisting to `analysis` and to the error list, the empty-repo path
  having empty `cycles`/`architecture_summary`, the summary populating from a
  mocked LLM call, and — critically — the summary degrading to `""` without
  raising when the LLM call fails.
- Full suite: `ruff check deploymint tests` clean; `pytest -m "not slow" -q` →
  209 passed, 1 pre-existing failure unrelated to this change (`checkov`
  binary not installed in this dev environment, reproduced identically on
  the pre-change tree via `git stash`).
