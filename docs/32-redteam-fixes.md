# 32. Two Red Team bugs fixed

Found while exploring the codebase for Phase 29+ (approval gate / Trivy / RAG
planning), fixed as a small self-contained item ahead of that larger work.

## Bug 1: `LLM_SEVERITY_CAP` was a lookup, not a clamp

`redteam.py`'s LLM adversarial layer capped severity with:

```python
f["severity"] = LLM_SEVERITY_CAP.get(f.get("severity"), f.get("severity", "low"))
```

`LLM_SEVERITY_CAP = {"critical": "high"}` only matches the exact lowercase
string `"critical"`. An LLM returning `"CRITICAL"` (or any casing/value
variant) missed the dict entirely and fell through to the raw string
unchanged — producing a `severity` value outside the `Finding.severity`
`Literal`. That value then:

- Was never counted by `warden.py`'s `SEVERITY_ORDER`-based threshold check
  (`SEVERITY_ORDER.index(...)` would raise `ValueError` if ever compared, but
  in practice it's just excluded from every level's count), so it never
  contributed to a block.
- Rendered on the run page but was silently invisible to the actual security
  gate — a critical-looking finding that could never block a deploy.

Fixed with a real `_clamp_llm_severity()`: lowercase + strip first, apply the
critical→high clamp, then validate against `SEVERITY_ORDER` — anything still
unrecognized (including `None`/empty string) becomes `"low"` rather than
silently vanishing from the gate.

## Bug 2: only 3 of 7 generated artifact types were ever red-teamed

The deterministic probe blob was:

```python
blob = "\n".join(str(artifacts.get(k, "")) for k in
                 ("dockerfile", "k8s_deployment", "k8s_service"))
```

`templates.py`'s `render_extra_artifacts()` generates and writes Terraform,
an Ansible playbook, a GitHub Actions workflow, and an ArgoCD application to
disk for every run — none of them were ever scanned by any of the 11
deterministic probes (`RT_CURL_PIPE_SH`, `RT_HARDCODED_SECRET`,
`RT_PRIVILEGED`, etc.). A reverse shell in a GitHub Actions workflow or a
hardcoded credential in a Terraform `provisioner "local-exec"` block would
pass through completely unnoticed.

Fixed by extending the blob to include `terraform`, `ansible_playbook`,
`github_actions_workflow`, and `argocd_application`.

## Verified

- `tests/test_redteam.py`: 13/13 passing, including 8 new tests — a
  dedicated `TestClampLlmSeverity` unit-testing the clamp function directly
  (lowercase, uppercase, mixed-case, unknown, `None`, empty string), an
  end-to-end regression test proving an uppercase `"CRITICAL"` LLM finding
  now clamps to `"high"` instead of vanishing, and two new probe-coverage
  tests confirming `RT_CURL_PIPE_SH` fires on a poisoned Terraform
  `local-exec` block and `RT_HARDCODED_SECRET` fires on a GitHub Actions
  workflow env var.
- Full suite: `ruff check deploymint tests` clean; `pytest -m "not slow" -q`
  → 218 passed, 1 pre-existing failure unrelated to this change (`checkov`
  binary not installed in this dev environment).
