# 34. Code Audit: a checklist over the user's actual source code

## Why this exists

Checkov, OPA, and Red Team all inspect what DeployMint itself *generates* —
the Dockerfile, Kubernetes manifests, Terraform. None of them ever look at
the user's actual application source code — the routes, config, auth
middleware being containerized and shipped. A dependency-CVE scanner (Trivy,
then Grype — see `docs/30-trivy.md`) was tried to add real vulnerability
coverage, but both needed a large signature database to download before the
first scan could even run, and even bootstrapped, returned 200+ findings that
were almost entirely low-severity noise in base OS packages nobody could act
on quickly.

Inspired by [benavlabs/vibe-check](https://github.com/benavlabs/vibe-check)'s
shape — a static checklist of concrete vulnerability categories, audited by
an AI reading the real code, not a signature lookup — but authored
specifically for what DeployMint's users actually ship (containerized
services, generated IaC), not vibe-check's SaaS-shaped list verbatim (their
Stripe/RLS-specific items mostly don't apply here).

## What it checks

`CODE_AUDIT_SYSTEM` (`core/prompts.py`): hardcoded secrets/API keys, a
committed `.env` with real-looking values, missing auth on an exposed
endpoint, SQL injection risk (string-built queries), wildcard CORS, debug
mode left on, insecure file uploads, weak password hashing, and
typosquatted dependency names. Explicitly excludes anything Checkov/OPA/Red
Team already cover — this agent's job is the application code, not the
deployment config.

## How it works

- **Where it runs**: a new graph node, `warden -> redteam -> code_audit ->
  gate` (`agents/graph.py`), gated by `enable_code_audit` (default on).
- **What it reads**: `core/repo_scanner.py`'s `walk_repo()` (already enforces
  `MAX_FILES`/`MAX_FILE_BYTES`/`.gitignore`/vendor-dir skipping) filtered to
  an extended extension list plus config/`.env`-shaped filenames regardless
  of extension (`AUDIT_EXTENSIONS`/`AUDIT_FILENAMES` in `agents/code_audit.py`).
- **Budget**: no prior art in this codebase for a multi-file token budget —
  `_order_by_criticality()` prioritizes `analysis["critical_files"]` (the
  same PageRank-ranked list `smith.py` already trims to) first, then falls
  back to remaining files, accumulating up to `MAX_AUDIT_CHARS` (60k chars).
  Files past the budget are named to the model as skipped, never silently
  dropped with a false claim of full coverage.
- **Output shape**: mirrors `redteam.py`'s `_llm_probe()` precedent exactly —
  `llm.complete_json(CODE_AUDIT_SYSTEM, user)`, iterate `findings`, force
  `source="code_audit"`, clamp severity through `warden.clamp_llm_severity`
  (moved there from `redteam.py` so both agents — and any future LLM-finding
  agent — share one implementation). Unlike Red Team's prompt, this one asks
  for `file`/`line` per finding, since real source files make a specific
  location meaningful (Red Team only ever audits single generated blobs).
  Same trust boundary as Red Team: an LLM-reported "critical" still clamps to
  "high", so a real block still requires a deterministic signal, per
  `docs/01-architecture.md §1.9`.
- **Schema**: `"code_audit"` added to `Finding.source`'s Literal;
  `code_audit_ran: NotRequired[bool]` added to `SecurityReport`, following
  the `redteam_ran` pattern. `counts` recomputed after appending findings —
  the same "warden.py only counts its own findings" gotcha `redteam.py`
  already documents.
- **UI**: no template restructuring needed — the severity-split view
  (`docs/33-deploy-lock-and-findings.md`'s Feature B) already renders any
  `source` value generically. Just a "Code Audit ran ✓/✗" badge added next to
  Checkov/OPA/Red Team's.

## Verified

- `tests/test_code_audit.py` (7 tests): disabled setting is a no-op; an empty
  repo makes zero LLM calls (nothing to audit); a planted secret in a real
  file produces a `code_audit`-sourced finding with the real file/line, and
  an LLM-reported "critical" clamps to "high"; an LLM failure produces an
  info-severity finding rather than crashing; `counts` recomputes correctly
  after this agent appends findings; critical files are prioritized in the
  read order; `.env`-shaped filenames are included regardless of extension
  while unrelated files (README.md) are excluded.
- `tests/test_graph.py`: `code_audit` node present by default, absent when
  `enable_code_audit=False`.
- `tests/test_warden.py`: `clamp_llm_severity` (moved from `redteam.py`)
  unit-tested directly (lowercase/uppercase/mixed-case/unknown/None/empty).
- Full suite: `ruff check deploymint tests` clean.
