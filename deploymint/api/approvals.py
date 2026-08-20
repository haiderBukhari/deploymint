"""The architecture approval gate: approve (or adjust) the plan shown after
the Architect node runs, then resume generation bound to those choices. See
docs/33-deploy-lock-and-findings.md.

Deliberately NOT a LangGraph resume/interrupt — see
runner/manager.py's `resume_from_approval()` docstring for why. This router
only validates the request and hands off to that function, following the
same "thin route, real work in manager.py" shape `api/runs.py` already uses.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from deploymint.db.database import get_db
from deploymint.db.models import Project, Run
from deploymint.runner.manager import resume_from_approval

router = APIRouter(tags=["approvals"])


class ApprovePlanRequest(BaseModel):
    replicas: int = Field(default=1, ge=1, le=20)
    cpu_request: str = "100m"
    cpu_limit: str = "500m"
    memory_request: str = "128Mi"
    memory_limit: str = "512Mi"
    port: int | None = None
    cloud_provider: Literal["aws", "gcp", "azure"] = "aws"
    provision_cluster: bool = False
    deploy_mode: Literal["kubernetes", "docker"] = "kubernetes"


@router.post("/api/runs/{run_id}/approve", status_code=202)
async def approve_plan(run_id: str, body: ApprovePlanRequest, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(409, f"run is {run.status}, not awaiting approval")
    project = db.get(Project, run.project_id)
    if not project:
        raise HTTPException(404, "project not found")

    await resume_from_approval(run, project, body.model_dump())
    return {"run_id": run_id, "status": "running"}
