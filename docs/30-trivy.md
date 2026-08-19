# 30. Trivy: real CVE scanning

## What this adds

Checkov and OPA catch *misconfigurations* (a privileged container, a missing
digest pin) — neither ever reports "this exact version of `requests` has a
known RCE." Trivy fills that gap: real, versioned CVEs in dependencies and OS
packages, plus its own misconfiguration checks as a bonus.

Two separate scans, at two different points in the pipeline:

- **Filesystem/config scan** (`scanners.run_trivy_fs`) — runs in the Warden's
  existing pre-deploy slot, alongside Checkov and OPA, against the generated
  artifacts directory. Can block the deploy like any other finding.
- **Image scan** (`scanners.run_trivy_image`) — runs from a new
  `agents/image_scan.py` node positioned *after* Execution, because the built
  image only exists once Execution has built it. Never blocks — the deploy
  already happened by the time this runs; findings surface on the run page
  for the next fix/redeploy cycle.

## Design notes

- **Trivy reports its own `Severity` per finding.** Unlike Checkov (whose
  severity comes from a hand-maintained 18-entry map, because free-tier
  Checkov emits none), Trivy's output just needs lowercasing
  (`TRIVY_SEVERITY`).
- **Same never-raise contract as Checkov/OPA**: `run_trivy_fs`/
  `run_trivy_image` return `(findings, error_string)`, never an exception.
  `trivy_available()` mirrors `checkov_available()`/`shutil.which("opa")`.
- **Trivy's absence alone does not fail the pipeline closed.** It's additive
  coverage on top of Checkov/OPA, not a replacement — the Warden's fail-closed
  check now considers all three scanners together (`not any([checkov_ran,
  opa_ran, trivy_ran])`), so Checkov/OPA still working is enough to proceed
  even with Trivy uninstalled.
- **`Finding.source`** widened to include `"trivy"`; **`SecurityReport`**
  gained two new keys — `trivy_ran` (pre-deploy fs scan) and
  `trivy_image_ran` (post-deploy image scan) — kept separate because they're
  genuinely different scans that can fail independently.
- **`enable_trivy` setting** (default on), following the existing
  `enable_redteam` convention — degrades cleanly to today's Checkov+OPA-only
  behavior when off, or when the binary isn't installed.
- Pinned in the `Dockerfile` the same way `kubectl`/`opa`/`terraform` already
  are — a specific released version, not `latest`.
- **The vulnerability DB is baked into the image at build time**
  (`trivy --download-db-only`, ~1.3GB uncompressed). A fresh container's
  first scan previously had to download the DB from cold, which took long
  enough to blow past the scan's own timeout — the run showed
  `trivy_ran: false` despite Trivy being installed and working. Baking it in
  makes every run's first scan as fast as any other.

## Verified

- `tests/test_scanners_trivy.py` (5 tests): not-installed path, a real vuln +
  misconfiguration parse (case-insensitive severity), a non-zero exit code,
  and unparseable output — all via mocked subprocess output, no real binary
  required to run the suite.
- `tests/test_warden.py` (+3 tests): Trivy findings merge into the report and
  set `trivy_ran`; Trivy's absence alone does not fail the run closed when
  Checkov/OPA still work; all three scanners failing together does fail
  closed, and the reason string now names Trivy too.
- `tests/test_image_scan.py` (5 tests): scans the built image and merges
  findings; never flips `passed` (the deploy already happened); no-ops when
  disabled or when there's no image tag; a scanner error sets
  `trivy_image_ran = False` without raising.
- `tests/test_graph.py` (+3 tests): `image_scan` node present when
  `enable_trivy` is on and deploy isn't skipped; absent when Trivy is
  disabled; absent when `skip_deploy=True` (Execution never runs, so there's
  no image to scan).
- Full suite: `ruff check deploymint tests` clean; `pytest -m "not slow" -q`
  → 234 passed (1 pre-existing failure unrelated to this change — the
  `checkov` binary isn't installed in this dev environment, only in the
  container).
- Real container: `docker compose exec app trivy --version` → `0.74.0`. A
  real end-to-end run against `fastapi-app` on a container without a
  pre-baked DB reproduced the cold-download timeout described above
  (`trivy_ran: false`, `trivy_image_ran: false`) — confirming the bug before
  fixing it. After baking the DB into the image and rebuilding, the same run
  against a fresh container completed in ~35s with `checkov_ran`, `opa_ran`,
  `trivy_ran`, and `trivy_image_ran` all `true`, and 201 real findings
  reported with `"source": "trivy"`.
