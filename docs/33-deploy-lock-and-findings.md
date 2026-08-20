# 33. Deploy lock, tamed findings list, and the architecture approval gate

Three features shipped together, referenced throughout `deploymint/`'s code
comments as this doc.

## Feature A: lock Deploy while a run is active

A project's Deploy button had no run-state awareness — a user could click it
again (or open two tabs) while a run was already in flight, kicking off a
second concurrent deploy with nothing stopping it.

- `deploymint/web/routes.py`'s `ACTIVE_RUN_STATUSES = ("pending", "running",
  "awaiting_approval")` — a project with a run in any of these statuses is
  "locked." `project_page()` computes `active_run` and passes it to the
  template; `deploy_project_form()` gets the same check as a 409 guard,
  defense-in-depth against a bypassed disabled button.
- `deploymint/web/templates/project.html`: the Deploy button gets `disabled`
  when `active_run`, with a link to that run's page.
- Re-enables live, no reload: `project.html` reuses `connectRun()`
  (`app.js`'s existing run-page WS watcher) pointed at `active_run.id` — its
  existing reload-on-`run.end` behavior naturally re-evaluates `active_run`
  server-side on reload, requiring zero new client-side logic.

## Feature B: show high-value findings, collapse the rest

A single run could return 200+ findings, almost all low-severity noise, in
one flat unstyled list — no way to tell "is this actually bad" without
reading every line.

- `deploymint/agents/state.py`: `SecurityReport` gained
  `counts: NotRequired[dict[str, int]]` — per-severity totals.
- `deploymint/agents/warden.py`: the per-severity `counts` dict (already
  computed for the `warden.done` event) is now persisted onto the report in
  both the passing and fail-closed branches. `redteam.py` and any later
  agent appending findings must recompute `counts` after appending — `warden.py`
  only ever counts its own findings.
- `deploymint/web/routes.py`'s `_split_by_severity(run)` splits findings into
  `critical_high` (always visible) and the rest (collapsed), later refined
  further into `your_deps`/`base_image` buckets keyed on a `package_type`
  field (from the now-removed Trivy/Grype work — see docs/30-trivy.md,
  docs/34-code-audit.md; `package_type` is currently only ever unset, so the
  `base_image` bucket stays empty until/unless a future scanner sets it
  again, which is harmless).
- `deploymint/web/templates/run.html`: a one-line posture summary from
  `run.security.counts`, critical/high findings always visible, the rest
  behind the existing `.step-more` `<details>` pattern already used
  elsewhere in this file.

## Feature C: architecture diagram + approval gate

The centerpiece: pick a folder → see the full architecture diagram and a
plan → approve or adjust a few knobs → *then* generate, with the approved
choices actually binding the output.

- **New graph flag**, mirroring `skip_deploy` exactly
  (`deploymint/agents/graph.py`): `build_graph(..., stop_after_architect=False)`
  — when set, only the `architect` node is added, wired straight to `END`.
- **New Run status**, `"awaiting_approval"` — `deploymint/db/models.py`'s
  `Run` gained `approved_plan: Mapped[dict | None] = mapped_column(JSONB)`
  (a manual `ALTER TABLE` is needed on any already-running dev Postgres,
  since `init_db()`'s `Base.metadata.create_all()` doesn't add columns to
  existing tables). `deploymint/schemas/run.py`'s `RunStatus` Literal had to
  be updated too — missed on the first pass, caused a real 500 on
  `GET /api/runs/{id}` until fixed.
- **Resuming — deliberately NOT a LangGraph checkpoint/interrupt.** This
  repo uses LangGraph at its simplest (no checkpointer, `astream()` takes no
  config), and the fire-and-forget task's `finally` block in `_execute`
  unconditionally tears down the bus/registry entry the moment a graph
  finishes — a real pause would fight that. Instead,
  `deploymint/runner/manager.py`'s `resume_from_approval()` hand-builds a
  `DeployState` from the persisted `Run` row and drives each remaining agent
  directly in sequence via `_run_from_smith()` — the same precedent
  `api/fixes.py`'s `apply_finding_fix` already established for a partial
  pipeline re-run.
- **Event sequence continuity across the pause/resume boundary** — a real
  bug found and fixed: a fresh `EventBus` for the resumed phase used to
  restart `seq` at 0, colliding with the paused phase's already-persisted
  seq numbers for the same `run_id`, silently failing every DB insert for
  the whole resumed phase (swallowed by `EventBus.emit()`'s broad except).
  `core/events.py`'s `EventBus`/`BusRegistry` now accept a `start_seq`,
  computed in `resume_from_approval()` as `MAX(seq)` for that run_id before
  creating the new bus.
- **Stale `run.end` replay** — a second real bug: `api/ws.py`'s replay loop
  used to forward every persisted event row unconditionally, including a
  stale `run.end` (status=`awaiting_approval`) from before the resume. The
  client treated that as "the run just finished," reloaded, replayed the
  same stale event, reloaded again — forever. `run.end` is now never
  replayed from history; it's always synthesized fresh from the run's
  *current* status.
- **New endpoint** `POST /api/runs/{run_id}/approve`
  (`deploymint/api/approvals.py`): 404 unknown run, 409 if not
  `awaiting_approval`, else stores the submitted knobs as `approved_plan`,
  flips status to `"running"`, and calls `resume_from_approval`.
- **Knobs**: `replicas`, `cpu_request`/`cpu_limit`, `memory_request`/
  `memory_limit`, `port`, `cloud_provider`, `provision_cluster`,
  `deploy_mode` (`"kubernetes"|"docker"`) — stored verbatim as the
  `approved_plan` JSONB blob (UI/API config, not `DeployState` schema).
- **Binding, not just displaying**: `deploymint/agents/templates.py`'s
  `render()`/`render_extra_artifacts()` accept an optional `approved_plan`
  and regenerate `k8s_deployment`/`k8s_service` with the approved values
  applied on top of the base template output. The LLM path
  (`deploymint/agents/smith.py`) gets its own binding step,
  `_apply_approved_plan()`, since it doesn't go through `templates.render()`
  at all — it patches the model's own generated YAML directly. Verified with
  a real run: submitted `replicas=3`/`port=9090`/custom CPU+memory, and the
  **LLM-generated** manifest genuinely contained those exact values.
- **`deploy_mode` was collected in the UI but never read by
  `execution.py`** — flagged here, actually wired up in
  docs/32-architect-thread-offload.md's sibling phase (Workstream 2, real
  local `kind` cluster support).

## Verified

- Full test suite (Features A/B/C combined): 247+/248 passing throughout
  this work, with the one persistent failure being the pre-existing
  checkov-binary-not-installed-locally issue, unrelated.
- Real container, end to end: registered a project, deployed, confirmed the
  run paused at `awaiting_approval` with a real diagram/plan rendered from
  that repo's actual analysis; submitted non-default knobs; approved;
  confirmed the resumed pipeline ran Checkov/OPA/Red Team correctly, the
  approved knobs appeared verbatim in the generated Kubernetes YAML, and the
  run reached a real terminal status with continuous event-seq numbering
  (no gaps, no collisions) and exactly one `run.end` on WebSocket replay.
