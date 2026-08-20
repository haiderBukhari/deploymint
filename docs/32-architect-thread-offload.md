# 32. Fixing the real homepage-hang bug: Architect blocking the event loop

## What was wrong

`ArchitectAgent.run()` was `async def`, but its entire body — `walk_repo()`,
`detect_language/framework/entrypoint/port/dependencies/microservices`,
`build_import_graph()` (per-file tree-sitter parsing), `rank_criticality()`,
`find_cycles()` (networkx) — was 100% synchronous CPU/disk work, executed
directly on the event loop.

The app runs as a **single uvicorn worker** (the Dockerfile's `CMD` has no
`--workers` flag) — one process, one event loop. Any synchronous blocking
call inside any `async def` handler freezes *everything* else concurrently
in flight, including a bare `GET /` with no relation to any run, for the
full duration of that call. This is why the homepage would intermittently
fail to load with no obvious connection to what was actually happening —
whenever any run's Architect phase was scanning a repo, the whole app froze
for however long that took. `max_concurrent_runs=2` let two Architect
phases overlap, doubling the stall window.

This is a different, more fundamental bug than the reload-loop / event-seq
issues fixed in the previous phase (docs/33-deploy-lock-and-findings.md) —
those were specific to the run page's WebSocket replay; this one affected
the entire app, for anyone, at any time a run's Architect phase ran.

## The fix

Mirrors the pattern `core/docker_engine.py`'s `build_image()` already uses
for its own blocking call (`asyncio.create_task(asyncio.to_thread(
_build_sync, ...))`):

- The synchronous chain was extracted into a private, module-level
  `_scan_and_analyze(root) -> dict` function in `agents/architect.py`,
  identical in behavior to what used to be inline in `run()`.
- `run()` now calls `scanned = await asyncio.to_thread(_scan_and_analyze,
  root)` — offloading the whole scan/parse/graph-build chain to a worker
  thread, freeing the event loop for its entire duration.
- `await self._summarize(analysis)` (the LLM call) and both
  `await self.emit(...)` calls stay on the event loop, unchanged — they're
  already correctly async, and moving them into the threaded call would
  pointlessly serialize LLM network I/O behind a thread-pool round trip.
- The walk-failure early-return (no `architect.done` emit, no summarize
  call, `_empty_analysis()` returned) is preserved exactly via a
  `walk_failed` flag in the returned dict.

## Secondary hardening: `walk_repo()` traversal pruning

`core/repo_scanner.py`'s `walk_repo()` used `sorted(root.rglob("*"))`, then
filtered `SKIP_DIRS` *after* each path was yielded — `Path.rglob` has no
directory-pruning hook, so it fully traversed into `.git`/`node_modules`/
`venv`/etc. before the filter ever ran, wasting `stat()` calls on
potentially thousands of irrelevant entries in a dependency-heavy repo.

Switched to `os.walk()` with in-place `dirnames[:] = sorted(d for d in
dirnames if d not in SKIP_DIRS)` pruning before descending — directories in
`SKIP_DIRS` are never entered at all now, not just filtered afterward. File
ordering within `result.files` is now directory-by-directory (top-down,
each level's files and subdirs sorted) rather than one globally sorted flat
path list — no test depended on the old exact interleaving, and this
doesn't change `MAX_FILES`/`MAX_FILE_BYTES`/`.gitignore` behavior at all.

## Verified

- `tests/test_architect.py`: 9/9 passing, including a new regression test
  (`test_scan_runs_off_the_event_loop_thread`) that spies on
  `_scan_and_analyze` and asserts it's called from a thread that is NOT
  `threading.main_thread()` — this would have failed against the original
  code and passes now.
- All existing architect tests (walk failure, empty repo, cycles, binary
  files, architecture summary LLM success/failure) pass unchanged — exact
  behavior parity confirmed, this is a pure execution-location change.
- `tests/test_smith.py`: 13/13 unaffected (Smith consumes Architect's
  output; no change to the output shape).
- Full suite: `ruff check` clean.
- Real container: rebuilt, triggered a real run, and confirmed `GET /`
  stayed fast (sub-second) while the run's Architect phase was executing —
  see the session's verification log for the exact `curl` timings.
