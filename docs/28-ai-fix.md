# 28 — One-click AI fix and re-scan

**Status: done.** Each security finding that maps to a real generated
artifact gets a "Suggest a fix" button: the configured LLM proposes a
minimal patch, the diff is shown, and applying it re-runs the security gate
against the patched artifact.

## The flow

1. **Suggest** — `POST /api/runs/{run_id}/findings/suggest-fix` with
   `{file, finding_id}`. `core/fix_suggester.py` sends the finding's
   id/message/remediation plus the artifact's *current* full content to
   `core.llm.complete()` (so whichever provider is configured — Anthropic,
   OpenAI, a local OpenAI-compatible runtime — works with no special
   casing), asking for the whole corrected file back with the minimum
   change. Read-only: nothing is written by this route.
2. **Review** — the diff is computed server-side with stdlib
   `difflib.unified_diff`, never asked of the model. An LLM-authored diff
   would be one more thing that could be subtly wrong, and the entire point
   of showing a diff is to be trustworthy about what actually changed. The
   UI colors `+`/`-`/`@@` lines.
3. **Apply & re-scan** — `POST /api/runs/{run_id}/findings/apply-fix` with
   `{file, patched_content}`.

## Why applying creates a new run instead of editing the old one

This product's pitch includes a tamper-evident audit trail. Letting an
already-verified run's artifacts change after the fact would undermine
exactly that guarantee — the run that "passed" would no longer be the run
whose contents were scanned.

So `apply-fix` creates a **new** `Run` row for the same project, carrying a
copy of the previous run's artifact set with the one patched file
substituted in, and re-runs `SecurityWardenAgent` against it. Both the
before and after states stay independently inspectable at their own URLs.

It re-runs only the security gate, not the full seven-stage pipeline — the
point is verifying the fix actually resolved the finding, not redeploying
anything. The new run's status is `success` (gate passed) or `blocked`
(still failing), and the response includes the remaining findings so the UI
can say what happened without a page reload.

## Two things the model can get wrong, handled

- **Markdown fences.** Models wrap file content in ``` fences despite being
  told not to; shipping that verbatim into a Dockerfile would corrupt it.
  `_strip_fences()` removes one leading/trailing fence pair, and only for a
  recognized language tag, so a file legitimately containing backticks
  isn't mangled.
- **A no-op "fix."** If the returned content matches the original, the
  response says `changed: false` and the UI reports "no fix suggested"
  rather than offering an Apply button that would create a pointless
  identical run.

## Which findings get a button

Only findings whose reported `file` resolves to an artifact this run
actually generated. Checkov reports a bare basename (`deploy.yml`) while the
artifact lives at a nested path (`.github/workflows/deploy.yml`), so
`web/routes.py`'s `_fixable_files()` matches on basename against the run's
own present artifacts. Findings with no real file — Red Team's `file: "-"` —
get no button at all rather than a button that would fail.

## Verified

- `tests/test_fix_suggester.py`: diff generation (added/removed lines,
  empty for identical content), fence stripping (tagged and bare),
  the unchanged-content case, and that an LLM failure propagates rather
  than being swallowed into a bogus "no change" result.
- `tests/test_api_fixes.py`: a real suggestion round-trip against a real
  finished run (LLM mocked), 404 for an unknown run, 400 for a path that
  isn't a generated artifact (including a traversal-shaped one), 404 for a
  finding not on that run, 502 when the LLM is unavailable, and — the
  important one — that applying a fix creates a new run with the patched
  artifact and a genuinely re-run security gate **while the original run's
  artifacts and status stay exactly as they were**.
