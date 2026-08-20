"""Deterministic Dockerfile/K8s templates — the fallback that guarantees a run
always produces artifacts. See docs/06-phase-2-generation.md §2.5."""

import re

from deploymint.schemas.artifacts import GeneratedArtifacts

PY_VERSION = "3.11"  # a safe, modern default — see the note in _python_fastapi()


def _entrypoint_module(entrypoint: str) -> str:
    """'main.py' -> 'main'; 'app/main.py' -> 'app.main'."""
    if not entrypoint:
        return "main"
    return entrypoint.removesuffix(".py").replace("/", ".")


def _labels(name: str) -> str:
    return f"app: {name}, managed-by: deploymint"


def _k8s_deployment(
    name: str, image: str, port: int, *, replicas: int = 1,
    cpu_request: str = "100m", cpu_limit: str = "500m",
    mem_request: str = "128Mi", mem_limit: str = "512Mi",
) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels: {{ {_labels(name)} }}
spec:
  replicas: {replicas}
  selector:
    matchLabels: {{ app: {name} }}
  template:
    metadata:
      labels: {{ app: {name} }}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
      containers:
        - name: {name}
          image: {image}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: {port}
          resources:
            requests: {{ cpu: "{cpu_request}", memory: "{mem_request}" }}
            limits:   {{ cpu: "{cpu_limit}", memory: "{mem_limit}" }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 10001
            capabilities: {{ drop: ["ALL"] }}
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          livenessProbe:
            httpGet: {{ path: /health, port: {port} }}
            initialDelaySeconds: 15
            periodSeconds: 20
          readinessProbe:
            httpGet: {{ path: /health, port: {port} }}
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        - name: tmp
          emptyDir: {{}}
"""


def _k8s_service(name: str, port: int) -> str:
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {name}-svc
  labels: {{ {_labels(name)} }}
spec:
  type: ClusterIP
  selector:
    app: {name}
  ports:
    - name: http
      port: {port}
      targetPort: {port}
"""


def _python_fastapi(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 8000)
    module = _entrypoint_module(analysis.get("entrypoint") or "main.py")
    # NOTE: analysis["python_version"] currently reflects the SCANNING host's
    # interpreter, not the target repo's declared version (Phase 1 doesn't parse
    # requires-python / runtime.txt yet). Templates deliberately pin a safe,
    # modern default instead of trusting that field — see docs/04-agents-spec.md.
    dockerfile = f"""\
# ---- builder ----
FROM python:{PY_VERSION}-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime ----
FROM python:{PY_VERSION}-slim
RUN groupadd -r appuser -g 10001 && \\
    useradd -r -u 10001 -g appuser -s /sbin/nologin appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser . .
USER 10001
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:{port}/health').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "{module}:app", "--host", "0.0.0.0", "--port", "{port}"]
"""
    dockerignore = "__pycache__/\n*.pyc\n.venv/\nvenv/\n.git/\n.deploymint/\n"
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore=dockerignore,
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic template: multi-stage build, non-root UID 10001, "
        "layer-cached dependency install, health-checked liveness/readiness probes.",
    )


def _python_flask(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    art = _python_fastapi(analysis, name, image)
    port = analysis.get("exposed_port", 5000)
    module = _entrypoint_module(analysis.get("entrypoint") or "app.py")
    art.dockerfile = art.dockerfile.replace(
        f'CMD ["python", "-m", "uvicorn", "{_entrypoint_module(analysis.get("entrypoint") or "main.py")}:app", "--host", "0.0.0.0", "--port", "{analysis.get("exposed_port", 8000)}"]',
        f'CMD ["python", "-m", "flask", "--app", "{module}", "run", "--host=0.0.0.0", "--port={port}"]',
    )
    return art


def _python_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    """No recognized framework — run the entrypoint directly with plain python."""
    port = analysis.get("exposed_port", 8000)
    entrypoint = analysis.get("entrypoint") or "main.py"
    dockerfile = f"""\
FROM python:{PY_VERSION}-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:{PY_VERSION}-slim
RUN groupadd -r appuser -g 10001 && \\
    useradd -r -u 10001 -g appuser -s /sbin/nologin appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser . .
USER 10001
EXPOSE {port}
CMD ["python", "{entrypoint}"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore="__pycache__/\n*.pyc\n.venv/\nvenv/\n.git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="No recognized framework — running the detected entrypoint directly.",
    )


def _node_express(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 3000)
    entrypoint = analysis.get("entrypoint") or "server.js"
    dockerfile = f"""\
FROM node:20-slim AS builder
WORKDIR /build
COPY package*.json ./
RUN npm ci --omit=dev

FROM node:20-slim
RUN groupadd -r appuser -g 10001 && useradd -r -u 10001 -g appuser appuser
WORKDIR /app
COPY --from=builder /build/node_modules ./node_modules
COPY --chown=appuser:appuser . .
USER 10001
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
  CMD node -e "require('http').get('http://localhost:{port}/health', r => process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
CMD ["node", "{entrypoint}"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore="node_modules/\n.git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic Node/Express template: prod-only npm install, non-root user.",
    )


def _node_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    return _node_express(analysis, name, image)


def _go_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 8080)
    entrypoint = analysis.get("entrypoint") or "main.go"
    dockerfile = f"""\
FROM golang:1.22-alpine AS builder
WORKDIR /build
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /app-bin ./{entrypoint.rsplit("/", 1)[0] if "/" in entrypoint else "."}

FROM alpine:3.19
RUN addgroup -g 10001 appuser && adduser -D -u 10001 -G appuser appuser
COPY --from=builder /app-bin /app-bin
USER 10001
EXPOSE {port}
CMD ["/app-bin"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore=".git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic Go template: multi-stage static build, distroless-ish alpine runtime.",
    )


def _java_generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    port = analysis.get("exposed_port", 8080)
    dockerfile = f"""\
FROM eclipse-temurin:21-jdk AS builder
WORKDIR /build
COPY . .
RUN ./mvnw -q -DskipTests package || ./gradlew -q build -x test

FROM eclipse-temurin:21-jre
RUN groupadd -r appuser -g 10001 && useradd -r -u 10001 -g appuser appuser
WORKDIR /app
COPY --from=builder /build/target/*.jar app.jar
USER 10001
EXPOSE {port}
CMD ["java", "-jar", "app.jar"]
"""
    return GeneratedArtifacts(
        dockerfile=dockerfile,
        dockerignore="target/\nbuild/\n.git/\n.deploymint/\n",
        k8s_deployment=_k8s_deployment(name, image, port),
        k8s_service=_k8s_service(name, port),
        reasoning="Deterministic Java template: multi-stage Maven/Gradle build.",
    )


def _generic(analysis: dict, name: str, image: str) -> GeneratedArtifacts:
    return _python_generic(analysis, name, image)


REGISTRY = {
    ("python", "fastapi"): _python_fastapi,
    ("python", "flask"): _python_flask,
    ("python", "django"): _python_fastapi,  # gunicorn swap is a template refinement; safe default for now
    ("python", "*"): _python_generic,
    ("javascript", "express"): _node_express,
    ("javascript", "*"): _node_generic,
    ("go", "*"): _go_generic,
    ("java", "*"): _java_generic,
}


def render(
    analysis: dict, project_name: str, image: str, approved_plan: dict | None = None,
) -> GeneratedArtifacts:
    """approved_plan is the knob set from the architecture approval gate
    (docs/33-deploy-lock-and-findings.md) — replicas/CPU/memory/port. Rather
    than threading it through every per-language generator above (they all
    share the same (analysis, name, image) signature), the base artifacts are
    generated exactly as before and then k8s_deployment/k8s_service are
    regenerated with the approved values applied on top — this is the "must
    actually bind the output, not just display it" requirement. None (the
    default, and every existing call site) leaves today's behavior untouched."""
    key = (analysis.get("language"), analysis.get("framework"))
    fn = REGISTRY.get(key) or REGISTRY.get((analysis.get("language"), "*")) or _generic
    art = fn(analysis, project_name, image)
    if approved_plan:
        port_match = re.search(r"targetPort:\s*(\d+)", art.k8s_service)
        default_port = int(port_match.group(1)) if port_match else analysis.get("exposed_port", 8000)
        port = approved_plan.get("port") or default_port
        art.k8s_deployment = _k8s_deployment(
            project_name, image, port,
            replicas=approved_plan.get("replicas", 1),
            cpu_request=approved_plan.get("cpu_request", "100m"),
            cpu_limit=approved_plan.get("cpu_limit", "500m"),
            mem_request=approved_plan.get("memory_request", "128Mi"),
            mem_limit=approved_plan.get("memory_limit", "512Mi"),
        )
        art.k8s_service = _k8s_service(project_name, port)
    return art


# ---------------------------------------------------------------------------
# Extra IaC artifacts (Terraform / Ansible / ArgoCD / GitHub Actions /
# Prometheus / Grafana). These are always DETERMINISTIC — never LLM-generated
# — because they're one step removed from "does the app run": generating them
# wrong doesn't break a deploy the way a bad Dockerfile does, but strict
# correctness (valid HCL, a real ServiceMonitor selector) matters more than
# per-repo customization for infra scaffolding like this. None of these are
# ever executed by DeployMint itself — they're written for the user to run
# (`terraform apply`, `ansible-playbook`, commit to a GitOps repo) on their
# own, so there's no execution-safety concern the way there is for the
# Dockerfile/K8s path. See docs/18-iac-generation.md.
# ---------------------------------------------------------------------------


def render_terraform(name: str, image: str, port: int, cloud: str = "aws") -> str:
    """Managed cloud cluster support (docs/19-managed-clusters.md): Terraform
    generation only — DeployMint never touches cloud credentials directly.
    The user runs `terraform apply` themselves with whatever AWS/GCP/Azure
    CLI auth they already have configured. `cloud` selects which provider's
    registry + optional managed cluster module gets generated; unrecognized
    values fall back to AWS."""
    if cloud == "gcp":
        return _render_terraform_gcp(name, port)
    if cloud == "azure":
        return _render_terraform_azure(name, port)
    return _render_terraform_aws(name, port)


def _render_terraform_aws(name: str, port: int) -> str:
    ecr_repo = name.replace("_", "-")
    return f"""\
terraform {{
  required_version = ">= 1.5"
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

variable "aws_region" {{
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}}

# EKS costs real money and takes ~15-20 minutes to provision — opt in
# explicitly rather than creating a cluster by default.
variable "create_cluster" {{
  description = "Provision a new EKS cluster. Leave false to just get the ECR repo and push into an existing cluster."
  type        = bool
  default     = false
}}

provider "aws" {{
  region = var.aws_region
}}

resource "aws_ecr_repository" "{ecr_repo}" {{
  name                 = "{ecr_repo}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {{
    scan_on_push = true
  }}
}}

resource "aws_ecr_lifecycle_policy" "{ecr_repo}" {{
  repository = aws_ecr_repository.{ecr_repo}.name
  policy = jsonencode({{
    rules = [{{
      rulePriority = 1
      description  = "Expire untagged images after 14 days"
      selection = {{
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }}
      action = {{ type = "expire" }}
    }}]
  }})
}}

module "eks" {{
  count   = var.create_cluster ? 1 : 0
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = "{ecr_repo}-cluster"
  cluster_version = "1.29"

  cluster_endpoint_public_access = true

  eks_managed_node_groups = {{
    default = {{
      instance_types = ["t3.medium"]
      min_size       = 1
      max_size       = 3
      desired_size   = {2 if port else 1}
    }}
  }}
}}

output "ecr_repository_url" {{
  value = aws_ecr_repository.{ecr_repo}.repository_url
}}

output "cluster_name" {{
  value = var.create_cluster ? module.eks[0].cluster_name : null
}}
"""


def _render_terraform_gcp(name: str, port: int) -> str:
    repo = name.replace("_", "-")
    return f"""\
terraform {{
  required_version = ">= 1.5"
  required_providers {{
    google = {{
      source  = "hashicorp/google"
      version = "~> 5.0"
    }}
  }}
}}

variable "gcp_project" {{
  description = "GCP project ID to deploy into."
  type        = string
}}

variable "gcp_region" {{
  description = "GCP region for the registry and cluster."
  type        = string
  default     = "us-central1"
}}

# GKE costs real money and takes several minutes to provision — opt in
# explicitly rather than creating a cluster by default.
variable "create_cluster" {{
  description = "Provision a new GKE cluster. Leave false to just get the Artifact Registry repo and push into an existing cluster."
  type        = bool
  default     = false
}}

provider "google" {{
  project = var.gcp_project
  region  = var.gcp_region
}}

resource "google_artifact_registry_repository" "{repo}" {{
  location      = var.gcp_region
  repository_id = "{repo}"
  format        = "DOCKER"
}}

resource "google_container_cluster" "{repo}" {{
  count    = var.create_cluster ? 1 : 0
  name     = "{repo}-cluster"
  location = var.gcp_region

  remove_default_node_pool = true
  initial_node_count       = 1
}}

resource "google_container_node_pool" "{repo}" {{
  count      = var.create_cluster ? 1 : 0
  name       = "{repo}-pool"
  location   = var.gcp_region
  cluster    = google_container_cluster.{repo}[0].name
  node_count = {2 if port else 1}

  node_config {{
    machine_type = "e2-medium"
  }}
}}

output "artifact_registry_url" {{
  value = "${{var.gcp_region}}-docker.pkg.dev/${{var.gcp_project}}/{repo}"
}}

output "cluster_name" {{
  value = var.create_cluster ? google_container_cluster.{repo}[0].name : null
}}
"""


def _render_terraform_azure(name: str, port: int) -> str:
    resource_name = name.replace("-", "").replace("_", "")
    display = name.replace("_", "-")
    return f"""\
terraform {{
  required_version = ">= 1.5"
  required_providers {{
    azurerm = {{
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }}
  }}
}}

variable "azure_location" {{
  description = "Azure region to deploy into."
  type        = string
  default     = "eastus"
}}

# AKS costs real money and takes several minutes to provision — opt in
# explicitly rather than creating a cluster by default.
variable "create_cluster" {{
  description = "Provision a new AKS cluster. Leave false to just get the ACR registry and push into an existing cluster."
  type        = bool
  default     = false
}}

provider "azurerm" {{
  features {{}}
}}

resource "azurerm_resource_group" "{resource_name}" {{
  name     = "{display}-rg"
  location = var.azure_location
}}

resource "azurerm_container_registry" "{resource_name}" {{
  name                = "{resource_name}acr"
  resource_group_name = azurerm_resource_group.{resource_name}.name
  location            = azurerm_resource_group.{resource_name}.location
  sku                 = "Basic"
  admin_enabled       = true
}}

resource "azurerm_kubernetes_cluster" "{resource_name}" {{
  count               = var.create_cluster ? 1 : 0
  name                = "{display}-aks"
  resource_group_name = azurerm_resource_group.{resource_name}.name
  location            = azurerm_resource_group.{resource_name}.location
  dns_prefix          = "{resource_name}"

  default_node_pool {{
    name       = "default"
    node_count = {2 if port else 1}
    vm_size    = "Standard_B2s"
  }}

  identity {{
    type = "SystemAssigned"
  }}
}}

output "acr_login_server" {{
  value = azurerm_container_registry.{resource_name}.login_server
}}

output "cluster_name" {{
  value = var.create_cluster ? azurerm_kubernetes_cluster.{resource_name}[0].name : null
}}
"""


def render_ansible(name: str, image: str, port: int) -> str:
    return f"""\
---
# Deploys {name} directly via Docker on a remote host — a different
# deployment model from the Kubernetes path (docker run, not a cluster).
# Run with: ansible-playbook -i <your-inventory> playbook.yml
- name: Deploy {name}
  hosts: "{{{{ target_hosts | default('all') }}}}"
  become: true
  vars:
    image: "{image}"
    container_name: "{name}"
    container_port: {port}

  tasks:
    - name: Ensure Docker is installed
      package:
        name: docker.io
        state: present

    - name: Ensure the Docker service is running
      service:
        name: docker
        state: started
        enabled: true

    - name: Pull the image
      community.docker.docker_image:
        name: "{{{{ image }}}}"
        source: pull

    - name: Remove any previous container with this name
      community.docker.docker_container:
        name: "{{{{ container_name }}}}"
        state: absent
      ignore_errors: true

    - name: Run the container
      community.docker.docker_container:
        name: "{{{{ container_name }}}}"
        image: "{{{{ image }}}}"
        state: started
        restart_policy: unless-stopped
        published_ports:
          - "{{{{ container_port }}}}:{{{{ container_port }}}}"
        healthcheck:
          test: ["CMD", "true"]
"""


def render_argocd(name: str, run_id: str) -> str:
    return f"""\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {name}
  namespace: argocd
  labels: {{ {_labels(name)} }}
spec:
  project: default
  source:
    # Point this at the git repo that holds the generated manifests —
    # DeployMint writes them under .deploymint/{run_id}/ in your own repo,
    # this just needs your remote URL filled in.
    repoURL: <YOUR_GIT_REPO_URL>
    targetRevision: HEAD
    path: .deploymint/{run_id}
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
"""


def render_github_actions(name: str, port: int) -> str:
    return f"""\
name: Build and push {name}

on:
  push:
    branches: [main]

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ghcr.io/${{{{ github.repository_owner }}}}/{name}:${{{{ github.sha }}}}

      # Swap this step for `kubectl apply` / `argocd app sync` / your own
      # deploy trigger once the registry above matches where you actually push.
      - name: Deployment reminder
        run: echo "Image pushed. Wire this job to your deploy step (kubectl apply, ArgoCD sync, etc.)."
"""


def render_prometheus_servicemonitor(name: str, port: int) -> str:
    return f"""\
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {name}
  labels: {{ {_labels(name)} }}
spec:
  selector:
    matchLabels:
      app: {name}
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
"""


def render_grafana_dashboard(name: str) -> str:
    import json

    dashboard = {
        "title": f"{name} — DeployMint",
        "uid": f"deploymint-{name}"[:40],
        "schemaVersion": 39,
        "tags": ["deploymint", name],
        "timezone": "browser",
        "panels": [
            {
                "id": 1, "title": "CPU usage",
                "type": "timeseries",
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "targets": [{
                    "expr": f'sum(rate(container_cpu_usage_seconds_total{{pod=~"{name}.*"}}[5m]))',
                    "legendFormat": "cpu",
                }],
            },
            {
                "id": 2, "title": "Memory usage",
                "type": "timeseries",
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                "targets": [{
                    "expr": f'sum(container_memory_working_set_bytes{{pod=~"{name}.*"}})',
                    "legendFormat": "memory",
                }],
            },
            {
                "id": 3, "title": "Request rate",
                "type": "timeseries",
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
                "targets": [{
                    "expr": f'sum(rate(http_requests_total{{job="{name}"}}[5m]))',
                    "legendFormat": "requests/s",
                }],
            },
            {
                "id": 4, "title": "Restarts",
                "type": "stat",
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
                "targets": [{
                    "expr": f'sum(kube_pod_container_status_restarts_total{{pod=~"{name}.*"}})',
                    "legendFormat": "restarts",
                }],
            },
        ],
    }
    return json.dumps(dashboard, indent=2)


def render_extra_artifacts(
    analysis: dict, name: str, image: str, run_id: str, cloud: str = "aws",
    approved_plan: dict | None = None,
) -> dict:
    """Always generated, regardless of whether the Dockerfile/K8s path used
    the LLM or the template fallback — see the module docstring above for why
    these stay deterministic. `cloud` picks the Terraform target
    (docs/19-managed-clusters.md); everything else is cloud-agnostic.
    approved_plan's port, if set, overrides analysis's — same binding
    requirement as render()'s k8s_deployment/k8s_service above."""
    port = (approved_plan or {}).get("port") or analysis.get("exposed_port", 8000)
    return {
        "terraform": render_terraform(name, image, port, cloud),
        "ansible_playbook": render_ansible(name, image, port),
        "argocd_application": render_argocd(name, run_id),
        "github_actions_workflow": render_github_actions(name, port),
        "prometheus_servicemonitor": render_prometheus_servicemonitor(name, port),
        "grafana_dashboard": render_grafana_dashboard(name),
    }
