# 35. Real local Kubernetes via `kind` — opt-in

## Why this exists

A local deploy with no reachable Kubernetes cluster silently fell back to a
plain `docker run` — the user asked to actually *see* the full deployment
happen on a real cluster locally, not just have YAML files generated that
never get applied anywhere. Separately, the approval gate's `deploy_mode`
knob (`kubernetes`/`docker`, docs/33-deploy-lock-and-findings.md) was
collected in the UI but never actually read by `execution.py` — picking
"docker run only" had zero effect.

This was confirmed as a **deliberate, documented scope decision** in this
project's own prior docs (`docs/00-prerequisites.md`, `docs/08-phase-4-execution.md`
§4.1b): "detect an already-reachable cluster, fall back to `docker run`
otherwise" — kind was always framed as something a developer sets up
manually on the host before testing, never something the app provisions
itself.

## What changed

- **`deploy_mode` is finally wired up.** `agents/execution.py`'s single gate
  now reads `state.get("approved_plan", {}).get("deploy_mode", "kubernetes")`.
  `"docker"` always short-circuits straight to `docker run`, regardless of
  cluster reachability — the explicit user choice actually does something
  now. `"kubernetes"` (the default) keeps today's implicit reachability
  check unchanged for everyone.
- **New `kube_engine.ensure_kind_cluster(name="deploymint")`** — checks
  `kind get clusters` for an existing cluster with that name; if absent,
  runs `kind create cluster --name deploymint`, using the same
  `run_command`/subprocess-streaming pattern every other function in that
  file already uses. Never raises: returns `True`/`False`.
- **Only invoked when `enable_auto_kind_cluster` is on** (new setting,
  **default `False`**) and `deploy_mode == "kubernetes"` and no cluster is
  currently reachable. On success, `cluster_reachable()` is re-checked and
  the existing kubectl-apply path runs unchanged; on failure (or the flag
  being off, the default), execution falls through to today's `docker run`
  path exactly as before — never a hang, never a raised error.
- `Dockerfile`: `kind` is now installed, pinned, alongside kubectl/opa/
  terraform.
- **`docker-compose.kind.yml`** — a separate, opt-in override file, applied
  with `docker compose -f docker-compose.yml -f docker-compose.kind.yml up
  -d --build`. The default `docker-compose.yml` is **unchanged** — adding
  `network_mode: host` there directly would have broken database
  connectivity for every user, since `db`'s service-name DNS only resolves
  on the default Compose bridge network. The override sets
  `network_mode: host` on `app` (needed so a `kind`-created cluster's
  kubeconfig `server:` address is reachable from inside the container — the
  existing `/var/run/docker.sock` mount alone is sufficient for creating the
  cluster, same as `docker build`/`docker run`, but not for reaching its API
  server afterward) and republishes `db` to `localhost` with a repointed
  `DATABASE_URL`, since host networking drops `app` off the bridge network
  the plain service-name hostname relied on.

## Platform caveat — Linux-only in practice today

Docker Desktop on macOS/Windows runs the Docker daemon inside a VM —
`network_mode: host` there means "host of the VM," not the actual host
machine, so it does **not** give the same reachability to a `kind`-created
cluster's API server that it does on native Linux. On those platforms,
`enable_auto_kind_cluster` is expected to fail closed to `docker run`
without hanging or erroring the run (verified — see below) rather than
actually reaching a real cluster. Users on macOS/Windows who want a real
local cluster should keep using their own pre-existing kind/Docker-Desktop-
Kubernetes cluster via the unchanged detection-only path (plain
`docker-compose.yml`, no override needed) — that path was already fully
working before this change and is untouched by it.

## Verified

- `tests/test_kube_engine.py` (6 tests): `ensure_kind_cluster` returns `True`
  without creating anything when the named cluster already exists, calls
  `kind create cluster` when absent, returns `False` (never raises) on a
  failed create or a missing `kind` binary (`FileNotFoundError`).
- `tests/test_execution_deploy_mode.py` (5 tests): `deploy_mode="docker"`
  always takes the docker-run branch even when a cluster is reachable;
  the ungated default path (no `approved_plan` at all) behaves exactly as
  before; `enable_auto_kind_cluster=False` (the default) never calls
  `ensure_kind_cluster` at all; enabling it tries to provision when
  unreachable and re-checks afterward; a provisioning failure falls through
  to `docker run` without raising.
- Full suite: `ruff check` clean.
- Real container, on this session's macOS host: with the flag enabled and
  no cluster reachable, confirmed the run falls through to `docker run`
  cleanly (no hang, no error surfaced) — matching the documented platform
  caveat. Real cluster creation + kubectl-apply reachability on a Linux host
  is the remaining verification step for whoever deploys this in a Linux
  dev environment.
