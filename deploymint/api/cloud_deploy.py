"""One-click "sync to AWS/Azure/GCP" for a finished run's Terraform module.
See docs/21-cloud-deploy.md for the design decision and its security model.

Credentials arrive in the POST body (over the same origin the dashboard is
already served from, never a query string) and are held only for the
duration of the single terraform subprocess call in core/terraform_exec.py —
this module never writes them to the database or a log line. Only the
run/apply *outcome* and its console output (which never contains the
credential values themselves) get persisted, mirroring how the rest of the
pipeline's audit trail works.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from deploymint.core.artifact_store import FILENAMES
from deploymint.core.cloud_creds import CloudCredentials, MissingCredentials, build_env
from deploymint.core.cloud_jobs import registry
from deploymint.core.terraform_exec import run_terraform
from deploymint.db.database import get_db, get_session_factory
from deploymint.db.models import Project, Run

router = APIRouter(tags=["cloud-deploy"])

_ACTIONS = ("plan", "apply", "destroy")


class CloudDeployRequest(BaseModel):
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = ""
    azure_subscription_id: str = ""
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    gcp_project: str = ""
    gcp_credentials_json: str = ""


def _resolve_dir(project: Project, run: Run) -> Path:
    return Path(project.repo_path) / ".deploymint" / run.id / Path(FILENAMES["terraform"]).parent


async def _execute(run_id: str, action: str, cloud: str,
                    directory: Path, creds: CloudCredentials) -> None:
    job = registry.start(run_id, action)
    Session = get_session_factory()

    async def on_line(line: str) -> None:
        await job.emit(line)

    ok = await run_terraform(action, directory, cloud, creds, on_line)
    await job.finish("success" if ok else "failed")

    with Session() as db:
        run = db.get(Run, run_id)
        if run:
            run.cloud_deploy_status = job.status
            run.cloud_deploy_action = action
            run.cloud_deploy_output = "\n".join(job.lines)[-50_000:]
            run.cloud_deployed_at = datetime.now(UTC)
            db.commit()


@router.post("/api/runs/{run_id}/cloud/{action}", status_code=202)
async def start_cloud_deploy(run_id: str, action: str, body: CloudDeployRequest,
                              db: Session = Depends(get_db)):
    if action not in _ACTIONS:
        raise HTTPException(404, f"unknown action: {action}")

    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "success":
        raise HTTPException(409, "only a successful run's Terraform module can be deployed")
    if not run.artifacts or not run.artifacts.get("terraform"):
        raise HTTPException(400, "this run has no Terraform module")
    if registry.is_running(run_id):
        raise HTTPException(409, "a cloud deploy is already running for this run")

    project = db.get(Project, run.project_id)
    if not project:
        raise HTTPException(404, "project not found")

    creds = CloudCredentials(**body.model_dump())
    try:
        build_env(project.cloud_provider, creds)  # validate only; env applied inside terraform_exec
    except MissingCredentials as e:
        raise HTTPException(400, str(e)) from e

    directory = _resolve_dir(project, run)
    if not directory.is_dir():
        raise HTTPException(400, "terraform module was not written to disk for this run")

    asyncio.create_task(_execute(run_id, action, project.cloud_provider, directory, creds))
    return {"status": "started", "action": action}


@router.get("/api/runs/{run_id}/cloud/status")
def cloud_deploy_status(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")

    job = registry.get(run_id)
    if job:
        return {"status": job.status, "action": job.action, "output": "\n".join(job.lines)}
    return {
        "status": run.cloud_deploy_status,
        "action": run.cloud_deploy_action,
        "output": run.cloud_deploy_output or "",
    }
