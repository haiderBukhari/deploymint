# 18 — Multi-Artifact IaC Generation (Terraform, Ansible, ArgoCD, GitHub Actions, Prometheus, Grafana)

**Status: done.** Originally scoped out of the MVP and confirmed out of scope again during
the Phase 17 gap analysis (`17-pending-work.md`) — then explicitly reversed and requested.
This doc records what was built and, more importantly, the design call that makes it safe.

---

## The one decision that shapes everything here

**These six artifacts are always deterministically templated — never LLM-generated.**

The Dockerfile and Kubernetes manifests are the one place DeployMint lets Claude write
the artifact (with a template fallback for resilience, not correctness). That's a
deliberate, narrow trust boundary: those two files get scanned by Checkov + OPA + Red
Team before anything runs, so a flawed LLM output is caught, not shipped blind.

Terraform, Ansible, ArgoCD, GitHub Actions, Prometheus, and Grafana don't get that same
scrutiny — Checkov covers two of them (see below), but there's no equivalent of the K8s
security policies for an Ansible playbook or a Grafana dashboard. Rather than extend the
LLM's authority into six more formats with no matching verification, these are generated
by hand-written, deterministic template functions in `agents/templates.py`. They are
correct by construction, not by scanning. This also means they're 100% reliable — no
API key, no rate limit, no model refusal can ever prevent them from being produced.

**None of these six are ever executed by DeployMint itself.** The Dockerfile gets built
and the K8s manifests get applied — that's the product's actual "deploy" action. These
six are scaffolding for the user to run on their own (`terraform apply`,
`ansible-playbook`, committing to a GitOps repo, pushing to trigger the CI workflow).
That removes any execution-safety question a wrong Terraform module might otherwise
raise — worst case, the user reads unhelpful HCL, not a live-fired plan.

---

## What's generated, and why each one looks the way it does

| File | Written to | Notes |
|---|---|---|
| `terraform/main.tf` | ECR repo + lifecycle policy, `create_cluster` var default `false` | EKS costs real money and takes ~15-20 min — opt in explicitly, never on by default |
| `ansible/playbook.yml` | Docker-based host deploy | A genuinely different deploy model than K8s — same image, remote host via SSH instead of a cluster |
| `argocd/application.yaml` | GitOps `Application` pointing at `.deploymint/<run_id>/` | References the manifests DeployMint already writes; user fills in their own repo URL |
| `.github/workflows/deploy.yml` | Build+push to GHCR on `main` | Highest-value artifact after the Dockerfile per the original proposal's own roadmap — deliberately simple (build+push only; the deploy trigger is left as a labeled next step, since that depends entirely on the user's own cluster/GitOps setup) |
| `monitoring/servicemonitor.yaml` | Prometheus Operator `ServiceMonitor` | Selector matches the app label already on every generated Deployment/Service; requires the generated K8s Service's port to be *named* `http` — `_k8s_service()` was updated to add that name specifically so this reference resolves |
| `monitoring/grafana-dashboard.json` | 4-panel dashboard (CPU, memory, request rate, restarts) | Generic PromQL against standard `kube-state-metrics`/cAdvisor metric names — works for any deployed service without per-repo customization |

All six are generated unconditionally on every run — `agents/smith.py` calls
`templates.render_extra_artifacts()` right after the Dockerfile/K8s step resolves
(LLM or template path, doesn't matter), and merges the result into the same
`artifacts` dict.

## Storage and serving

Written under the same `.deploymint/<run_id>/` directory as the Dockerfile/K8s
manifests, in their natural subpaths (`core/artifact_store.py`'s `FILENAMES` map):
`terraform/main.tf`, `ansible/playbook.yml`, `argocd/application.yaml`,
`.github/workflows/deploy.yml`, `monitoring/servicemonitor.yaml`,
`monitoring/grafana-dashboard.json`. Each is served individually at
`/api/runs/{id}/artifacts/{path}` — the route uses FastAPI's `{filename:path}`
converter so nested paths with slashes resolve correctly.

The run page's Artifacts panel gained six more tabs alongside the original four,
using the same inline preview (plain-escaped text for `.tf`/`.json`, the existing
YAML/Dockerfile highlighters for the rest — see `17-pending-work.md` §17.4 for why
there's no full syntax-highlighting library vendored for this).

## Security scanning

Checkov natively supports both `terraform` and `github_actions` as scan frameworks —
`core/scanners.py`'s `--framework` list was extended to include them alongside the
existing `dockerfile`/`kubernetes`. Verified for real (not mocked): the generated
Terraform module reliably produces `CKV_TF_1` (module source not pinned to a commit
hash) and `CKV_AWS_136` (ECR not KMS-encrypted); the generated workflow produces
`CKV2_GHA_1` (top-level permissions not explicit). All three are `medium` severity —
below the default `block_severity` threshold, so they show up as findings without
blocking a deploy, same as any other non-critical finding.

Ansible, ArgoCD, and Grafana dashboards aren't covered by Checkov's framework list and
have no equivalent scanner wired in — this is a known, accepted gap, not an oversight.

## Verified

- All six generators produce syntactically valid output (YAML/JSON parse cleanly;
  Terraform is checked for balanced braces as a crude HCL sanity check, since it isn't
  YAML/JSON)
- The full pipeline (register → deploy → generate → write → scan) run against the real
  containerized stack, not just the test suite — confirmed all six files present, all
  ten artifact tabs rendering on the run page, and Checkov actually walking the new
  Terraform/GitHub Actions files with real findings
- `tests/test_iac_generation.py` covers each generator's shape; `test_warden.py` adds a
  real (non-mocked) Checkov subprocess test proving the new frameworks are active
