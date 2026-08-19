# 22 — Project name sanitization

**Status: done.** A project's name is used as a Docker image tag, a Kubernetes
resource name, and a Terraform resource identifier — all three formats reject
spaces, uppercase, and most punctuation. `core/naming.py`'s `slugify()` is the
one place that turns whatever a user typed into something all three accept.

## The bug this fixes

`schemas/project.py`'s `ProjectCreate` already validated and sanitized `name`
via a pydantic `field_validator` — but only requests hitting the JSON API
(`POST /api/projects`) go through that model. The web dashboard's own
registration form (`web/routes.py`'s `register_project_form`) built a
`Project` directly from the raw HTML form field, bypassing it completely.

Registering `bew proj` (a space in the name) through the web UI — the
primary way anyone actually uses this app — sailed straight into the
database. Every pipeline stage up through Security Warden and Red Team
succeeded, because none of them care what the name looks like. Only at
Execution, when `agents/execution.py` built `f"deploymint/{name}:{run_id}"`
as the actual Docker image tag, did it fail:

```
execution: invalid tag 'deploymint/bew proj:run_f592928eafdd': invalid reference format
```

By then the run had already burned real LLM calls (Smith's Claude/OpenAI
completion) and real subprocess time (Checkov, OPA, Red Team) before failing
— a slow, confusing way to discover a typo-shaped problem that should have
been caught in under a millisecond at registration time.

Found by actually registering a project through the web form with a space
in the name and watching the run fail, not by code review — the same
pattern as every other bug this project's docs record.

## The fix

- `core/naming.py`: extracted the sanitization logic (lowercase, replace
  anything that isn't alphanumeric/`-`/`_` with `-`) out of the pydantic
  validator into a standalone `slugify()` function.
- `schemas/project.py`'s validator now calls it, unchanged in behavior.
- `web/routes.py`'s `register_project_form` now calls it too, on the same
  code path the JSON API always used — the actual fix. An invalid name (one
  that's *entirely* non-alphanumeric, e.g. `"---"`) re-renders the
  registration page with a 400 and an error message instead of creating a
  broken project.
- The registration form's placeholder/title now say up front that the name
  becomes a Docker tag and Kubernetes/Terraform resource name, so spaces and
  uppercase getting silently rewritten to hyphens isn't a surprise.

## Verified

- `tests/test_naming.py`: `slugify()` directly — spaces, uppercase, mixed
  punctuation, and the all-punctuation rejection case.
- `tests/test_web.py`: posting a name with a space through
  `/projects/register` creates a project whose stored `name` has no spaces
  and would be a valid Docker/Kubernetes identifier.
- Re-ran the exact failure end to end against the real container: the same
  "bew proj" input now becomes `bew-proj` at registration, and the run that
  previously failed at Execution with an invalid Docker reference completes
  successfully all the way through FinOps.
