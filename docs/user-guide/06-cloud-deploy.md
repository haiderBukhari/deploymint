# Cloud Deploy

Everything above generates a Terraform module for you to run yourself — but
a successful run's page also has a **Cloud Deploy** card that can run
`terraform plan`/`apply` against a real AWS, Azure, or GCP account directly,
without leaving the dashboard.

## What it does

1. Pick **Run Plan** — DeployMint runs `terraform init` + `plan` against the
   real cloud account you enter credentials for, streaming the actual
   terraform output live.
2. Once the plan succeeds, tick the confirmation checkbox and click
   **Apply** — this genuinely creates or modifies real cloud infrastructure
   and can incur real cost. It's disabled until a Plan has succeeded, on
   purpose.

## What it does *not* do with your credentials

- Credentials are sent once, for that single action, and are never written
  to the database, a log line, or disk — GCP's service-account JSON is the
  one exception that needs a temporary file at all, and it's deleted the
  moment the terraform process exits, success or failure.
- Nothing here changes your Terraform-only default from
  [Generated Artifacts](/guide/iac-formats) — you're always free to skip this
  panel and run `terraform apply` yourself from the downloaded module
  instead.

## Which cloud

The project's cloud target (set at registration, or editable via the API)
decides which credential fields you'll see — AWS (access key/secret/region),
Azure (subscription/tenant/client ID/secret), or GCP (project ID + a service
account JSON).
