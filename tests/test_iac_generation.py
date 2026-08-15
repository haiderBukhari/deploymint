"""Terraform / Ansible / ArgoCD / GitHub Actions / Prometheus / Grafana are
always deterministically generated on every run, regardless of whether the
Dockerfile/K8s path used Claude or the template fallback. See
docs/18-iac-generation.md and agents/templates.py's render_extra_artifacts()."""

import json

import pytest
import yaml

from deploymint.agents.smith import ArtifactSmithAgent
from deploymint.agents.templates import render_extra_artifacts, render_terraform

ANALYSIS = {
    "language": "python", "framework": "fastapi", "package_manager": "pip",
    "entrypoint": "main.py", "exposed_port": 8000, "python_version": "3.11",
    "dependencies": [], "critical_files": [], "has_tests": True, "file_count": 3,
}


def test_render_extra_artifacts_produces_all_six():
    extra = render_extra_artifacts(ANALYSIS, "my-app", "deploymint/my-app:run_1", "run_1")
    assert set(extra) == {
        "terraform", "ansible_playbook", "argocd_application",
        "github_actions_workflow", "prometheus_servicemonitor", "grafana_dashboard",
    }
    for v in extra.values():
        assert v.strip()


def test_terraform_has_ecr_repo_and_optional_eks():
    extra = render_extra_artifacts(ANALYSIS, "my-app", "img", "run_1")
    tf = extra["terraform"]
    assert "aws_ecr_repository" in tf
    assert 'variable "create_cluster"' in tf
    assert "default     = false" in tf  # EKS opt-in, not default-on
    assert tf.count("{") == tf.count("}")  # crude balance check for hand-written HCL


def test_ansible_playbook_is_valid_yaml_and_uses_the_image():
    extra = render_extra_artifacts(ANALYSIS, "my-app", "deploymint/my-app:run_1", "run_1")
    doc = yaml.safe_load(extra["ansible_playbook"])
    assert isinstance(doc, list)
    assert "deploymint/my-app:run_1" in extra["ansible_playbook"]


def test_argocd_application_is_valid_and_points_at_the_run_dir():
    extra = render_extra_artifacts(ANALYSIS, "my-app", "img", "run_42")
    doc = yaml.safe_load(extra["argocd_application"])
    assert doc["kind"] == "Application"
    assert doc["spec"]["source"]["path"] == ".deploymint/run_42"


def test_github_actions_workflow_is_valid_yaml_with_build_push():
    extra = render_extra_artifacts(ANALYSIS, "my-app", "img", "run_1")
    doc = yaml.safe_load(extra["github_actions_workflow"])
    assert "jobs" in doc
    assert "docker/build-push-action" in extra["github_actions_workflow"]


def test_prometheus_servicemonitor_selector_matches_service_label():
    extra = render_extra_artifacts(ANALYSIS, "my-app", "img", "run_1")
    doc = yaml.safe_load(extra["prometheus_servicemonitor"])
    assert doc["kind"] == "ServiceMonitor"
    assert doc["spec"]["selector"]["matchLabels"]["app"] == "my-app"
    assert doc["spec"]["endpoints"][0]["port"] == "http"


def test_k8s_service_names_its_port_http_for_servicemonitor_compatibility():
    from deploymint.agents.templates import render

    art = render(ANALYSIS, "my-app", "img")
    svc = yaml.safe_load(art.k8s_service)
    assert svc["spec"]["ports"][0]["name"] == "http"


def test_grafana_dashboard_is_valid_json_with_panels():
    extra = render_extra_artifacts(ANALYSIS, "my-app", "img", "run_1")
    dashboard = json.loads(extra["grafana_dashboard"])
    assert dashboard["panels"]
    assert all("targets" in p for p in dashboard["panels"])


@pytest.mark.asyncio
async def test_smith_always_includes_extra_artifacts_on_the_template_path():
    out = await ArtifactSmithAgent().run(
        {"run_id": "run_smith1", "project_id": 1, "project_name": "t",
         "repo_path": "/workspace/t", "force": False, "errors": [], "current_node": "",
         "analysis": ANALYSIS}
    )
    art = out["artifacts"]
    assert art["generated_by"] == "template"
    for key in ("terraform", "ansible_playbook", "argocd_application",
                "github_actions_workflow", "prometheus_servicemonitor", "grafana_dashboard"):
        assert art[key].strip()


# --- Managed cloud cluster support (docs/19-managed-clusters.md) ---
# Terraform-only: DeployMint generates the module, the user runs
# `terraform apply` themselves with their own cloud credentials already
# configured. DeployMint never stores or transmits a cloud credential.

def test_terraform_defaults_to_aws():
    assert "aws_ecr_repository" in render_terraform("my-app", "img", 8000)


def test_terraform_gcp_has_artifact_registry_and_optional_gke():
    tf = render_terraform("my-app", "img", 8000, cloud="gcp")
    assert "google_artifact_registry_repository" in tf
    assert "google_container_cluster" in tf
    assert 'variable "create_cluster"' in tf
    assert "default     = false" in tf  # GKE opt-in, not default-on


def test_terraform_azure_has_acr_and_optional_aks():
    tf = render_terraform("my-app", "img", 8000, cloud="azure")
    assert "azurerm_container_registry" in tf
    assert "azurerm_kubernetes_cluster" in tf
    assert 'variable "create_cluster"' in tf
    assert "default     = false" in tf  # AKS opt-in, not default-on


def test_terraform_unknown_cloud_falls_back_to_aws():
    assert "aws_ecr_repository" in render_terraform("my-app", "img", 8000, cloud="oracle")


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure"])
def test_terraform_brace_balance_for_every_cloud(cloud):
    """Crude HCL sanity check — same one used for the AWS-only test before
    multi-cloud support existed."""
    tf = render_terraform("my-app", "img", 8000, cloud=cloud)
    assert tf.count("{") == tf.count("}")


@pytest.mark.asyncio
async def test_smith_generates_the_projects_own_cloud_target():
    out = await ArtifactSmithAgent().run(
        {"run_id": "run_smith2", "project_id": 1, "project_name": "t",
         "repo_path": "/workspace/t", "force": False, "errors": [], "current_node": "",
         "analysis": ANALYSIS, "cloud_provider": "gcp"}
    )
    assert "google_artifact_registry_repository" in out["artifacts"]["terraform"]


@pytest.mark.asyncio
async def test_smith_defaults_to_aws_when_cloud_provider_is_absent():
    """Old runs / any state dict built before this feature existed won't
    have cloud_provider at all — must not crash, must default to aws."""
    out = await ArtifactSmithAgent().run(
        {"run_id": "run_smith3", "project_id": 1, "project_name": "t",
         "repo_path": "/workspace/t", "force": False, "errors": [], "current_node": "",
         "analysis": ANALYSIS}
    )
    assert "aws_ecr_repository" in out["artifacts"]["terraform"]
