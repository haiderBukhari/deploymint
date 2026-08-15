# 19 — Managed Cloud Cluster Support (EKS/GKE/AKS)

**Status: done, Terraform-only.** Confirmed with the user before building — the
alternative (DeployMint storing and using cloud credentials directly to provision
clusters) was explicitly ruled out as much bigger scope requiring real credential
handling and a security review neither of us wanted to take on right now.

---

## What this actually is

Each project now has a `cloud_provider` field (`aws` | `gcp` | `azure`, defaults to
`aws`), set at registration. The generated `terraform/main.tf` (already part of Phase
18's IaC generation) is provider-aware:

| Cloud | Registry | Optional managed cluster (opt-in, never default-on) |
|---|---|---|
| AWS (default) | ECR | EKS |
| GCP | Artifact Registry | GKE |
| Azure | ACR | AKS |

**DeployMint never touches a cloud credential.** The generated Terraform module is
written to `.deploymint/<run_id>/terraform/main.tf`, same as every other generated
artifact — the user runs `terraform apply` themselves, using whatever AWS CLI / gcloud
/ az CLI authentication they already have configured on their own machine. This is the
same trust boundary as every other Terraform/Ansible/ArgoCD/GitHub Actions artifact
DeployMint generates (see `18-iac-generation.md`): DeployMint writes files, it does not
execute them.

Every cloud's module follows the same pattern already established for AWS/EKS: a
`create_cluster` variable defaulting to `false`, because provisioning a managed
cluster costs real money and takes several minutes — never something that happens
just because a project exists.

## Why not have DeployMint provision the cluster itself?

That was the other option on the table, and it's a fundamentally different, much
larger feature: real credential storage (encrypted at rest, scoped IAM roles, a UI for
managing them), direct cloud API calls instead of generated code the user reviews
before running, and a security posture DeployMint doesn't currently have anywhere else
in the codebase (compare to `01-architecture.md` §1.7, which is explicit that the
Docker socket mount is "root-equivalent host access" and says so plainly — a stored
AWS credential with cluster-provisioning permissions is an even bigger blast radius,
and deserves at least that level of scrutiny before being built). Terraform-only
sidesteps all of that: the user reviews the generated `.tf` file exactly like they'd
review a generated Dockerfile, and decides for themselves whether to run it.

## Where it's exposed

- **Registration**: a cloud provider dropdown on the project registration form
  (`index.html`) and the equivalent `POST /api/projects` JSON field
  (`cloud_provider`)
- **Project page**: shown as a stat tile ("Terraform target")
- **Generation**: `agents/templates.py`'s `render_terraform(name, image, port, cloud)`
  dispatches to `_render_terraform_aws/_gcp/_azure` — threaded through
  `runner/manager.py`'s state dict (`cloud_provider`, defaulting to `"aws"` for any run
  triggered before this field existed) down to `agents/smith.py`'s call to
  `render_extra_artifacts()`

## Verified

- Each cloud's module: correct registry resource, correct optional-cluster resource
  gated behind `create_cluster` (default `false`), balanced braces (a crude HCL sanity
  check — `terraform` itself isn't a dependency of this project, so this doesn't
  replace `terraform validate`, but catches an obviously malformed template)
- `cloud_provider` threads end-to-end: registering a project with `cloud_provider=gcp`
  and deploying it produces a GCP-flavored `terraform/main.tf`
- Old/pre-existing runs with no `cloud_provider` in their state dict default to `aws`
  rather than crashing

## Explicitly not done

- Real `terraform validate`/`terraform plan` execution (would require bundling
  Terraform into the image — a real dependency addition, not attempted here)
- OAuth-style "connect your AWS/GCP/Azure account" flow
- Any cloud credential storage, transmission, or direct API usage
