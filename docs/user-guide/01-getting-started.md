# Getting started

## 1. Have the stack running

DeployMint runs as a Docker Compose app on your own machine. If it's not
already up:

```bash
docker compose up -d
```

The dashboard is at `http://localhost:8000` once it's healthy.

## 2. Put your repo where DeployMint can see it

DeployMint only ever looks inside `./projects` on your machine (mounted into
the container as `/workspace`) — it can't reach anywhere else on your
filesystem. Copy or symlink your project in:

```bash
cp -r /path/to/your/app ./projects/my-app
```

## 3. Register it

Pick whichever interface you prefer — all three do the same thing underneath.

**Web dashboard** — go to `/dashboard`, fill in the name and
`/workspace/my-app` as the repo path, pick a cloud target (only matters if
you'll use Terraform/Cloud Deploy later), and click **Register**.

**CLI**:
```bash
deploymint up ./projects/my-app --name my-app
```
This registers *and* triggers the first deploy in one step — see
[CLI Reference](/guide/cli).

**Chat**: open the chat box and say `deploy my-app` — if the project isn't
registered yet it'll tell you so.

A project's name becomes part of a Docker image tag and Kubernetes/Terraform
resource names, so it's automatically lowercased and hyphenated — `My App`
becomes `my-app`.

## 4. Deploy it

From the project's page, click **Deploy**. You'll land on the run page and
watch all seven pipeline steps happen live — the Dockerfile and manifests
appearing as Artifact Smith finishes, security findings as Warden scans them,
real build/deploy output streaming into the terminal, and a cost estimate at
the end.

## 5. Read the result

- **Green "success"** — it's deployed. The Deployment & Evidence card shows
  the image tag, where it's running, and a clickable local URL if it's on
  plain `docker run`.
- **Red "blocked"** — the security gate stopped it. Check the Security
  section for which finding blocked it and why.
- **Red "failed"** — something broke during build or deploy. Check the
  Errors section and the terminal output — both show the real failure
  reason, not just a status code.

## What you don't need to write yourself

Nothing. No Dockerfile, no Kubernetes YAML, no Terraform module — DeployMint
generates all of it from what it finds in your repo. If you want to see
exactly what it wrote, every generated file is one click away in the
Artifacts panel on the run page.
