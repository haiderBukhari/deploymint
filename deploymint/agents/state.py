# FROZEN SCHEMA — see docs/01-architecture.md §1.5.
# Changing a key here means touching every agent, the DB JSONB columns, and the UI.
# If you think you need a new key, add it to an existing sub-TypedDict instead.

from typing import Literal, NotRequired, TypedDict


class RepoAnalysis(TypedDict):
    language: str
    framework: str
    package_manager: str
    entrypoint: str
    exposed_port: int
    python_version: str
    file_count: int
    dependencies: list[str]
    services: list[dict]
    graph: dict
    critical_files: list[str]
    has_tests: bool
    dockerfile_exists: bool
    architecture_summary: NotRequired[str]  # LLM-generated plain-English narrative
    cycles: NotRequired[list[list[str]]]  # each entry is a cycle, as a list of file paths


class Artifacts(TypedDict):
    dockerfile: str
    dockerignore: str
    k8s_deployment: str
    k8s_service: str
    generated_by: Literal["llm", "template"]
    model_used: str
    reasoning: NotRequired[str]  # Claude's own explanation; empty on the template path
    # A longer, structured version of the above — per-artifact rationale and
    # what was rejected, not just a 2-3 sentence summary. Added as a new key
    # rather than widening `reasoning` itself, per this file's frozen-schema
    # rule — empty on the template path, same as `reasoning`. See
    # docs/29-richer-reasoning.md.
    reasoning_detail: NotRequired[str]
    # Always deterministically generated regardless of generated_by — see
    # agents/templates.py's render_extra_artifacts() module docstring for why.
    terraform: NotRequired[str]
    ansible_playbook: NotRequired[str]
    argocd_application: NotRequired[str]
    github_actions_workflow: NotRequired[str]
    prometheus_servicemonitor: NotRequired[str]
    grafana_dashboard: NotRequired[str]


class Finding(TypedDict):
    id: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    source: Literal["checkov", "opa", "redteam", "code_audit"]
    file: str
    line: NotRequired[int]
    message: str
    remediation: str
    explanation: NotRequired[str]  # LLM-generated plain-English "why this matters"


class SecurityReport(TypedDict):
    passed: bool
    findings: list[Finding]
    checkov_ran: bool
    opa_ran: bool
    redteam_ran: bool
    # Set by the Code Audit agent (agents/code_audit.py) — an LLM-driven
    # checklist over the user's actual source files. Replaces the Trivy/Grype
    # dependency-CVE scanner, which was tried and dropped: both required a
    # large vulnerability DB download before the first scan could run, and
    # even bootstrapped, returned 200+ mostly-noise base-OS-package findings.
    # See docs/34-code-audit.md.
    code_audit_ran: NotRequired[bool]
    # Per-severity totals across all findings, e.g. {"critical": 1, "high": 3,
    # "medium": 5, "low": 190, "info": 6} — already computed in warden.py for
    # the warden.done event; persisted here too so the run page can render a
    # one-line posture summary without recomputing it. See docs/33-deploy-lock-and-findings.md.
    counts: NotRequired[dict[str, int]]
    blocked_reason: NotRequired[str]


class Deployment(TypedDict):
    image_tag: str
    build_log: str
    session_file: str
    kubectl_output: str
    mode: NotRequired[Literal["kubernetes", "docker"]]
    pod_name: NotRequired[str]
    container_id: NotRequired[str]
    local_url: NotRequired[str]
    status: Literal["not_started", "building", "deploying", "running", "failed", "rolled_back"]
    anomaly_explanation: NotRequired[str]


class CostReport(TypedDict):
    source: Literal["estimate", "aws_ce", "sample_json"]
    monthly_usd: float
    breakdown: dict[str, float]
    recommendations: list[str]


class DeployState(TypedDict):
    # --- inputs (set once, never mutated) ---
    run_id: str
    project_id: int
    project_name: str
    repo_path: str
    force: bool
    cloud_provider: NotRequired[str]  # aws | gcp | azure — which Terraform module Smith generates

    # --- agent outputs (each node writes exactly one key) ---
    analysis: NotRequired[RepoAnalysis]
    artifacts: NotRequired[Artifacts]
    security: NotRequired[SecurityReport]
    deployment: NotRequired[Deployment]
    cost: NotRequired[CostReport]

    # --- control ---
    errors: list[str]
    current_node: str
