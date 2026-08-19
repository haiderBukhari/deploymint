# 26 — Fixing a real redeploy conflict

**Status: done.** Redeploying the same project in `docker run` mode (no
Kubernetes cluster reachable) could fail with Docker's own "Conflict...
already in use" error on a genuine back-to-back redeploy.

## The bug, caught in a real run

A pasted failed run (`run_6b1a4ef67b84`) showed:

```
docker: Error response from daemon: Conflict. The container name
"/fastapi-new-app" is already in use by container "c2a170b5bb...".
```

`core/docker_run.py`'s `run_container()` already ran `docker rm -f {name}`
before `docker run` — the redeploy path reuses the same container name on
purpose, so the daemon needs the old one actually gone first — but it never
checked whether that removal worked. If it raced with the previous
container still stopping, or failed for any other reason, `docker run` then
hit the daemon's own name conflict.

## The fix

`run_container()` now:
1. Captures the `docker rm -f` result (previously discarded entirely), and
   forwards `**kw` (recorder/audit/on_line) to it too — its output used to
   never reach the terminal or audit log at all.
2. If the following `docker run` fails with a message containing
   "conflict" or "already in use" (case-insensitive), retries once: remove
   again, run again.
3. Gives up and surfaces the failure if the retry also fails — no infinite
   loop.

This mirrors the same self-healing step the generated Ansible playbook
already encodes for the identical scenario (`ignore_errors: true` on its
own "remove any previous container" task) — the manual deploy path just
hadn't caught up to it.

## Verified

- `tests/test_docker_run_conflict_retry.py`: 5 tests, all mocking
  `core.runner.run_command` — first-try success, a conflict-then-success
  retry, giving up after one failed retry, not retrying on an unrelated
  failure (e.g. "no such image"), and confirming `**kw` reaches both the
  cleanup and the run call so output is actually logged.
