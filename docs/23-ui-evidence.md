# 23 — Fixing the blank terminal, surfacing real evidence per step

**Status: done.** The run page now shows why a run failed (not just a red badge),
fixes two real event-handling bugs, and adds a Deployment & Evidence card so it's
unmistakable each pipeline stage did real, verifiable work — not "just an LLM."

## What "blank terminal" actually was

Tracing the full emit → WebSocket → render chain (`agents/execution.py` →
`core/events.py` → `api/ws.py` → `web/static/app.js`) found no mismatch in the
data path: the event type (`execution.log`) and payload key (`line`) match
exactly end to end, and a direct DB query confirmed real runs really do persist
dozens of `execution.log` rows with the actual `docker build` output.

The actual bug was in `app.js`'s terminal-rendering logic, found by opening a
raw WebSocket connection in the browser and diffing what arrived against what
rendered: **for an already-finished run, the WS replay sends every persisted
event — all of them, execution.log included — essentially instantly, with no
real-time pacing.** `connectRun` batches terminal lines into a buffer and
flushes it into the DOM on a 100ms `setInterval`. On a live, in-progress run
that's fine — events trickle in one at a time and the interval reliably fires
between them. But on an already-finished run (the overwhelmingly common case —
anyone opening or reloading a run page they know already completed), `run.end`
— the last event in the replay — can arrive well within that first 100ms tick.
`run.end`'s handler called `clearInterval(flush)` immediately, discarding
every line still sitting in the buffer that the timer never got a chance to
drain. The terminal rendered permanently blank despite the real build output
having been sent correctly over the socket the entire time.

Fixed by extracting the flush logic into a named `flushTerminal()` function and
calling it once, synchronously, before `clearInterval` in the `run.end`
handler — so any buffered lines are always written out no matter how fast the
replay runs.

A second, related gap made this worse: the terminal *is* correctly empty when
a Docker build fails before producing any stdout at all — e.g. an invalid
image tag rejected immediately by Docker (see [22-naming.md](22-naming.md)).
The generic `error` WS event had no handler in `app.js`, and the persisted
`run.errors` list was never rendered anywhere in `run.html` — so a fast
failure looked exactly like the blank-terminal bug above, with nothing else on
the page explaining why. Both are fixed below.

## Two more bugs found while tracing this

**A WebSocket replay race** (`api/ws.py`'s `stream_run`): the DB replay query ran
to completion *before* `bus.subscribe()` was called. Any event emitted in that
window landed in neither the already-fetched replay batch nor the not-yet-
subscribed live queue — silently dropped. Fixed by subscribing first and
deduping against a `max_sent` watermark, so the worst case is an event arriving
in both places (harmless) instead of neither (lost).

**`redteam.probe` events never rendered.** `app.js` shared one handler for
`warden.finding` and `redteam.probe`, gated on `payload.id` — but a Red Team
probe payload only ever has `probe_name`/`result`, never `id`. The condition was
always false, so live "probe hit" notifications silently never appeared. Split
into its own case rendering `probe_name` directly into the Red Team step's detail.

## What changed

- **Errors/Notes section** (`run.html`): renders `run.errors` when present. A
  failed/blocked run gets a red "Errors" heading; a *successful* run that still
  has entries (e.g. Smith's own resilience-path note, "fell back to template
  because no LLM key is configured") gets a muted "Notes" heading instead —
  found while testing this exact change: a normal local-dev run without an
  Anthropic/OpenAI key always has a non-empty `errors` list, and labeling that
  a bright-red "Error" next to a green success badge would be actively
  misleading.
- **Live `error` handling** (`app.js`): a new `case "error":` appends the
  message to both the failing step's own detail area and the terminal (prefixed
  `[error]`) as soon as it happens — no page reload needed to find out why a run
  died.
- **Terminal start/end markers** (`agents/execution.py`): a `[deploymint] ...`
  line at the start of each stage (build, kind-load, kubectl apply, rollout,
  docker run) and a summary line on success or failure, via a small `_mark()`
  helper next to the existing `_line()` callback. The terminal no longer feels
  inert during a slow-starting or quiet subprocess, and always shows *something*
  even on an instant failure.
- **Deployment & Evidence card** (`run.html`): a new card, server-rendered from
  `run.deployment`/`run.security` exactly like the existing Cost/Security cards
  — no schema change needed, this data was already being computed and stored,
  just never shown. Shows the real image tag, mode (kubernetes/docker), pod or
  container id, a clickable local URL, the Oracle's verdict, and — the part
  that most directly answers "is this just an LLM?" — explicit
  `Checkov ran ✓` / `OPA ran ✓` / `Red Team ran ✓` badges pulled straight from
  `security.checkov_ran`/`opa_ran`/`redteam_ran`. The full build log and
  `kubectl` output are included in collapsed `<details>` blocks — real command
  output, one click away, without cluttering the page by default.

## What was deliberately left out of this pass

Per-finding remediation text, the cost breakdown by container, Oracle's raw
CPU/memory/restart time series, and stdout/stderr color-coding in the terminal
are all real data the earlier audit found with nowhere to go — but were scoped
out of this round in favor of the bug fixes and the one new evidence card. See
the session's own decision log for the three-tier scope options that were
weighed.

## Verified

- `tests/test_ws.py`: connecting to a run's WebSocket from the very start now
  produces the exact same sequence of events a full post-hoc replay does — no
  gaps, no duplicates (the regression test for the subscribe/query race).
- `tests/test_web.py`: the Errors/Notes distinction (red vs muted, based on
  run status), the Deployment & Evidence card's fields and evidence badges, and
  the "no deployment for this run" fallback message when Execution was skipped.
- Manually verified against the real containerized stack: a run that fails fast
  now shows its failure reason in three places (the Errors section, the live
  step detail, and the terminal) instead of zero; a real successful
  docker/kubectl execution shows start/end markers in the terminal and the new
  evidence card populated with the actual image tag and scanner-ran badges.
