"""Writes generated artifacts to disk for scanning. See docs/07-phase-3-security.md §3.2."""

import json
from datetime import datetime, timezone
from pathlib import Path

FILENAMES = {
    "dockerfile": "Dockerfile",
    "dockerignore": ".dockerignore",
    "k8s_deployment": "k8s-deployment.yaml",
    "k8s_service": "k8s-service.yaml",
}


def write_artifacts(run_id: str, repo_path: str, artifacts: dict) -> Path:
    """Written under the project's own .deploymint/ subfolder, inside the mounted
    workspace — never elsewhere. See 01-architecture.md §1.7 and §1.8."""
    d = Path(repo_path) / ".deploymint" / run_id
    d.mkdir(parents=True, exist_ok=True)
    for key, fname in FILENAMES.items():
        content = artifacts.get(key)
        if content:
            (d / fname).write_text(content)
    (d / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "generated_by": artifacts.get("generated_by"),
        "model_used": artifacts.get("model_used"),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return d
