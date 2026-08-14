from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from deploymint.db.database import get_db
from deploymint.db.models import Project, Run
from deploymint.runner.manager import cancel_run, start_run
from deploymint.schemas.run import RunCreate, RunRead

router = APIRouter(tags=["runs"])

_ARTIFACT_FILENAMES = {
    "Dockerfile": "dockerfile",
    ".dockerignore": "dockerignore",
    "k8s-deployment.yaml": "k8s_deployment",
    "k8s-service.yaml": "k8s_service",
}


@router.post("/api/projects/{project_id}/runs", status_code=202)
async def trigger_run(project_id: int, body: RunCreate, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    run_id = await start_run(
        project, force=body.force, trigger=body.trigger, skip_deploy=body.skip_deploy
    )
    return {"run_id": run_id, "status": "pending"}


@router.get("/api/runs", response_model=list[RunRead])
def list_runs(project_id: int | None = None, status: str | None = None,
              limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(Run)
    if project_id is not None:
        q = q.filter(Run.project_id == project_id)
    if status is not None:
        q = q.filter(Run.status == status)
    return q.order_by(Run.created_at.desc()).limit(limit).all()


@router.get("/api/runs/{run_id}", response_model=RunRead)
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@router.post("/api/runs/{run_id}/cancel")
async def cancel(run_id: str, db: Session = Depends(get_db)):
    if not db.get(Run, run_id):
        raise HTTPException(404, "run not found")
    return {"cancelled": await cancel_run(run_id)}


@router.get("/api/runs/{run_id}/artifacts")
def get_artifacts(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if not run.artifacts:
        raise HTTPException(400, "no artifacts for this run yet")
    return run.artifacts


@router.get("/api/runs/{run_id}/artifacts/{filename}")
def get_artifact_file(run_id: str, filename: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    key = _ARTIFACT_FILENAMES.get(filename)
    if not key or not run.artifacts or key not in run.artifacts:
        raise HTTPException(404, f"no such artifact: {filename}")
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(run.artifacts[key])
