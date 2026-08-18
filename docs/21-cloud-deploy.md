# 21 — Cloud Deploy (one-click terraform apply)

**Status: done.** A "Cloud Deploy" panel on a successful run's page runs the generated
Terraform module — `plan` then `apply` — against a real AWS, Azure, or GCP account.

## Why this exists, and why it's a deliberate change from Phase 19

[19-managed-clusters.md](19-managed-clusters.md) chose Terraform-only on purpose: DeployMint
generates a module, you run `terraform apply` yourself, and DeployMint never holds cloud
credentials. That was the right default, but it means "sync my local testing to AWS/Azure"
was still a manual step outside the app.

This phase adds an explicit, opt-in escalation on top of that default — not a replacement
for it. You can still ignore this panel entirely and run the Terraform module by hand
exactly as before. When you do use the panel, three things stay true:

- **Credentials are per-invocation, not stored.** They're POSTed once, held only for the
  duration of a single `terraform` subprocess call, and never written to the database, a
  log line, or disk (GCP's service-account JSON is the one exception that needs a file at
  all — it's written to a private 0600 tempdir and deleted in a `finally` the moment the
  process exits, whether it succeeded or crashed).
- **No shell interpolation.** Every terraform invocation goes through
  `asyncio.create_subprocess_exec` with an argument list — never `shell=True` — so a
  credential value can never be read as shell syntax.
- **Apply requires a real decision, not a stray click.** The UI disables the Apply button
  until a Plan has succeeded *and* a checkbox acknowledging real cost/changes is ticked.
  The API also refuses to deploy anything for a run that isn't `status == "success"` —
  you can't apply infrastructure for a build the security gate blocked.

## How it works

- `core/cloud_creds.py` — validates the credential shape per cloud and maps fields to the
  exact env vars each Terraform provider reads natively (`AWS_ACCESS_KEY_ID` /
  `ARM_CLIENT_SECRET` / etc.) — no provider config files needed.
- `core/terraform_exec.py` — runs `terraform init` then `plan`/`apply`/`destroy` as a
  subprocess in the run's own `.deploymint/<run_id>/terraform/` directory (the same
  directory `artifact_store.py` already wrote the module to for Checkov scanning), streaming
  every output line to a callback. Per-action timeouts (`apply`/`destroy`: 15 minutes) stop
  a hung process from hanging a request forever.
- `core/cloud_jobs.py` — an in-memory registry of one live job per run, deliberately
  separate from `core/events.py`'s per-run `EventBus`. That bus is created at run start and
  dropped the instant the run finishes; a cloud deploy happens later, on demand, so it needs
  its own lifecycle independent of the run's.
- `api/cloud_deploy.py` — `POST /api/runs/{run_id}/cloud/{plan|apply|destroy}` validates the
  run succeeded and has a Terraform artifact, guards against a second deploy starting while
  one is already running, then kicks the job off in the background and returns immediately.
  `GET /api/runs/{run_id}/cloud/status` lets a reconnecting browser tab pick up where it left
  off.
- `ws.py`'s `/ws/deploy/{run_id}` streams the job's console output live, replaying anything
  already buffered before tailing new lines — same shape as the run's own `/ws/runs/{id}`.
- The run page's Cloud Deploy card only renders for a successful run with a Terraform
  artifact, shows only the credential fields the project's `cloud_provider` needs, and keeps
  Apply disabled until Plan succeeds and the cost/risk checkbox is checked.

## What this does NOT do

- It does not store or remember your cloud credentials for next time — every deploy from
  the UI means re-entering them. If that's ever worth trading for convenience, that's the
  "fully connected account" option that was explicitly turned down when this was scoped —
  see the decision recorded in this session, not a code file.
- It does not run automatically as part of the pipeline. Cloud Deploy is a manual,
  after-the-fact action a person takes on a run they've already reviewed — it is not wired
  into `runner/manager.py`'s automatic Architect → Smith → Warden → Red Team → Execution →
  Oracle → FinOps sequence.

## Getting terraform into the container

The Dockerfile now installs a pinned `terraform` binary (via HashiCorp's release zip,
requiring `unzip`) the same way it already pins `kubectl` and `opa` — a specific version,
not "latest", so a HashiCorp release doesn't silently change behavior under this image.

## Verified

- `core/cloud_creds.py`: every cloud's required-field validation, both the happy path and
  every missing-field combination, without needing a real terraform binary.
- `core/terraform_exec.py`: subprocess argument construction, streaming callback behavior,
  and timeout handling — verified against a mocked subprocess, not a real cloud account (no
  test suite should ever need real AWS/Azure/GCP credentials to pass).
- `api/cloud_deploy.py`: 404 on an unknown run, 409 on a non-`success` run or a second
  concurrent deploy, 400 on missing credentials or a missing Terraform artifact.
