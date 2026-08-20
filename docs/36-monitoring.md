# 36. Monitoring: global encrypted credentials, fleet status, per-agent performance

## Why this exists

Cloud Deploy's credential form asked for AWS keys on every single deploy
action — the user wanted to set them once for the whole system instead.
Separately, there was no single place to see every project's currently
deployed status, or how each specialized agent (Architect, Smith, Warden,
Red Team, Code Audit, Execution, Oracle, FinOps) is actually performing.

## Encrypted cloud credentials — a deliberate security decision

This codebase had zero at-rest secret storage anywhere before this —
Cloud Deploy's credentials were explicitly request-scoped and never
persisted; `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are plain environment
variables, never in the database. Storing AWS/Azure/GCP credentials at rest
was confirmed as the wanted approach over the zero-new-storage env-var
alternative, so real encryption infrastructure was added:

- **New dependency**: `cryptography` (Fernet symmetric encryption) — the
  first crypto dependency in this codebase.
- **New table**, `CloudCredential` (`deploymint/db/models.py`): one row per
  cloud (`aws`/`azure`/`gcp`, unique), holding only `encrypted_blob` —
  Fernet ciphertext of the JSON-serialized credential fields. Saving again
  replaces the row; nothing is ever appended/duplicated.
- **The encryption key never lives next to the ciphertext.** A new required
  env var, `DEPLOYMINT_SECRET_KEY` (a Fernet key, generated once via `python
  -c "from cryptography.fernet import Fernet;
  print(Fernet.generate_key().decode())"`), read via
  `config.py`'s `secret_key` property — the exact same env-var-only pattern
  `anthropic_api_key`/`openai_api_key` already use. If unset (or invalid),
  `core/credential_store.py` raises `SecretKeyMissing`, which every
  credential-saving endpoint turns into a clear 400 — it never silently
  stores anything with a default/weak key.
- **`core/credential_store.py`**: `encrypt`/`decrypt` wrap Fernet directly;
  `save_credentials`/`load_credentials`/`delete_credentials`/
  `credential_status` are the only DB touchpoints. Never logs a plaintext
  value at any point, mirroring `core/cloud_creds.py`'s existing discipline.
  `credential_status()` returns presence + `updated_at` only — never the
  decrypted values, never even a masked secret.
- **Cloud Deploy still works exactly as before when the form is filled
  in** — a per-request credential always wins. A blank field falls back to
  the stored value for that cloud (`api/cloud_deploy.py`'s
  `start_cloud_deploy`); if both are blank, the existing
  `MissingCredentials` 400 still fires exactly as before this change.
- **New endpoints**, `deploymint/api/settings.py`: `GET
  /api/settings/credentials` (status only), `POST
  /api/settings/credentials/{cloud}` (save/replace), `DELETE
  /api/settings/credentials/{cloud}` (forget).

## The Monitoring page

New route `GET /monitoring`, new template `monitoring.html`, linked in the
nav. Four sections:

1. **Cloud credentials** — configured/not-configured badges per cloud, a
   form to save/forget, reusing the same field sets `CloudDeployRequest`
   already defines.
2. **Fleet status** — every project's latest run + deployment snapshot
   (`Run.deployment`'s `status`/`mode`/`local_url`), reusing the exact
   per-project query pattern `web/routes.py`'s `dashboard()` already uses
   for `latest_runs`. A "Recheck now" button fans out a real, on-demand
   health check (`docker_run.container_healthy()` /
   `kube_engine.get_pod_name()`) across every currently-"running" entry,
   capped at `MAX_RECHECK` (25) so a large fleet can't block the page —
   deliberately a button, not a background poller (that scope belongs to
   the separate drift-watcher feature).
3. **Agent performance** — a new aggregation over the `events` table.
   `agents/graph.py`'s `_wrap()` already emits `node.enter`/`node.exit`
   (with `ms`) for all 8 agents on every run; this was zero new
   instrumentation, purely a new query (`core/monitoring.py`'s
   `agent_performance()`) computing avg/median/max duration and an error
   rate (joined against `error`-type events carrying the same `node`) per
   agent across the last 50 runs.
4. **LLM cost by model** — reuses `schemas/run.py`'s existing
   `_pricing_for()`/pricing table, grouped by `model_used` across recent
   runs.

Charts are **hand-rolled inline SVG bars** — no new charting library. This
project has a repeatedly-documented no-CDN, self-contained-vendor policy
(only `cytoscape.min.js`/`htmx.min.js` are vendored), so a new dependency
wasn't the right call for this.

## Known gap found while verifying: token usage was never captured

Verifying the cost section against the real container surfaced a genuine,
pre-existing gap unrelated to this feature: `Run.input_tokens`/
`output_tokens` are columns on the model and `schemas/run.py`'s
`llm_cost_usd` already computes from them, but **nothing anywhere in the
LLM provider layer (`core/llm.py`, `core/providers/*.py`) has ever captured
token usage from a real API response** — `complete_raw()` returns only the
completion text. So `llm_cost_usd` has always been `None` for every run in
this project's history, and this section's chart correctly shows "No
LLM-generated runs yet" even after 16+ real runs, since none of them ever
had token counts recorded. The Agent Performance section (timing, from
`node.enter`/`node.exit`) is unaffected and shows real numbers from actual
runs. Fixing token capture is a separate, larger change (provider return
shape, every call site, `manager.py`'s persistence) — flagged as a
follow-up, not bundled into this feature.

## Verified

- `tests/test_credential_store.py` (6 tests): encrypt/decrypt round-trip,
  plaintext never recognizable in the ciphertext, missing/invalid secret
  key raises `SecretKeyMissing` rather than silently storing, save/load/
  delete round-trip, `credential_status()` never leaks a decrypted value.
- `tests/test_settings_api.py` (4 tests): status endpoint, save requires a
  secret key (400 without one), save→status→forget round-trip, unknown
  cloud 404s.
- `tests/test_monitoring.py` (6 tests): fleet status lists every project
  including ones with no runs yet; `recheck_health` only checks
  currently-"running" entries; `agent_performance` correctly aggregates
  avg/error-rate from real `events` rows and zeroes out agents with no
  data; `run_cost_summary` correctly prices tokens by model.
- `tests/test_api_cloud_deploy.py` (+2 tests, 9 total): a blank form
  actually uses a saved credential end-to-end; a per-request credential
  still overrides the stored one when both are present.
- Full suite: `ruff check` clean; `pytest -m "not slow" -q` → 277 passed, 1
  pre-existing failure unrelated (checkov binary not installed locally).
