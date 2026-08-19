# Generated artifacts — the six formats

Every successful run generates all of these, available in the Artifacts
panel on the run page and downloadable individually. Dockerfile and
Kubernetes manifests are LLM-generated (with a deterministic fallback); the
rest are always deterministic templates — deliberately, since a wrong
Terraform resource name is a different (and cheaper-to-get-wrong) mistake
than a wrong Dockerfile `USER` line.

| File | What it's for | Use it when... |
|---|---|---|
| **`Dockerfile`** + `.dockerignore` | Builds your app into a container image — multi-stage, non-root user, health-checked. | Always — this is what Execution actually builds and runs. |
| **`k8s-deployment.yaml`** + `k8s-service.yaml` | Deploys the image to Kubernetes with resource limits, security context, and liveness/readiness probes already set. | You have (or want) a Kubernetes cluster. |
| **`terraform/main.tf`** | Provisions the cloud side — an ECR/Artifact Registry/ACR repository, and *optionally* a managed cluster (EKS/GKE/AKS, off by default since it costs real money). | You want your own cloud infrastructure, not just a running container. See [Cloud Deploy](/guide/cloud-deploy) to run it from the dashboard directly. |
| **`ansible/playbook.yml`** | Deploys the image directly to a remote host via `docker run` — a different model from the Kubernetes path, for a single VM instead of a cluster. | You're deploying to a plain VM, not Kubernetes. |
| **`argocd/application.yaml`** | A GitOps `Application` manifest pointing at wherever you push the generated manifests. | You run ArgoCD and want DeployMint's output synced automatically. |
| **`.github/workflows/deploy.yml`** | Builds and pushes the image to GitHub Container Registry on every push to `main`. | You want CI to build the image instead of doing it locally. |
| **`monitoring/servicemonitor.yaml`** + `grafana-dashboard.json` | Wires the deployment into an existing Prometheus + Grafana stack — CPU, memory, request rate, restart count panels, pre-built. | You already run Prometheus/Grafana and want this deployment to show up in it. |

None of the Terraform/Ansible/ArgoCD/GitHub Actions/Prometheus/Grafana files
are executed by DeployMint automatically — they're written for you to run
yourself (`terraform apply`, `ansible-playbook`, committing to a GitOps repo)
except Terraform, which you *can* run directly from the dashboard — see
[Cloud Deploy](/guide/cloud-deploy).
