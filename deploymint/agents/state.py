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


class Artifacts(TypedDict):
    dockerfile: str
    dockerignore: str
    k8s_deployment: str
    k8s_service: str
    generated_by: Literal["llm", "template"]
    model_used: str


class Finding(TypedDict):
    id: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    source: Literal["checkov", "opa", "redteam"]
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

    # --- agent outputs (each node writes exactly one key) ---
    analysis: NotRequired[RepoAnalysis]
    artifacts: NotRequired[Artifacts]
    security: NotRequired[SecurityReport]
    deployment: NotRequired[Deployment]
    cost: NotRequired[CostReport]

    # --- control ---
    errors: list[str]
    current_node: str
