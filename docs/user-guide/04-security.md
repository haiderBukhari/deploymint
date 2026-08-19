# Security & compliance

Every generated artifact goes through the same gate before anything runs —
this isn't optional or configurable per-project.

## What actually scans your artifacts

- **Checkov** — 550+ built-in rules covering Dockerfile, Kubernetes, and
  Terraform misconfigurations (root users, missing resource limits, exposed
  secrets, overly broad IAM, and far more). This is a real subprocess scan,
  not an LLM guessing what looks risky.
- **Open Policy Agent (OPA)** — custom Rego policies layered on top of
  Checkov for checks specific to this project's own security bar.
- **Red Team** — an adversarial pass looking for the kind of subtle issue a
  rules-based scanner wouldn't catch: an unpinned base image, a
  prompt-injection-shaped string, a supply-chain red flag.

You can confirm all three actually ran on a given deploy — the run page's
Deployment & Evidence card shows explicit `Checkov ran ✓` / `OPA ran ✓` /
`Red Team ran ✓` badges, not just a pass/fail summary.

## Reading a finding

Each finding shows:
- **Severity** — `critical`/`high`/`medium`/`low`/`info`.
- **The rule ID** and a plain message (e.g. `CKV_DOCKER_3` — "Ensure the last
  USER is not root").
- **An explanation**, in plain language, for critical/high findings — not
  just the rule ID.

## What actually blocks a deploy

Any **critical** or **high** severity finding blocks the deploy outright —
the pipeline stops at the Security Warden step and nothing gets built or
deployed. Medium/low/info findings are recorded and shown, but don't block.

If both Checkov and OPA are somehow unavailable in the environment, the
Warden fails **closed** — it blocks the deploy rather than silently skipping
the security check. You'll see this reflected in the evidence badges (both
showing "did not run ✗") and a clear reason in the run's Errors section.

## Force-deploying past a block

If you're certain a blocking finding is a false positive for your case, the
JSON API and CLI both accept a `force` flag to deploy anyway. There's no
force option in the web dashboard's Deploy button — this is deliberate,
since bypassing the security gate should be an explicit, considered choice,
not a default click.
