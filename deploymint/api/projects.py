from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from deploymint.agents.architect import ArchitectAgent
from deploymint.core.sandbox import SandboxError, validate_repo_path
from deploymint.db.database import get_db
from deploymint.db.models import Project
from deploymint.schemas.project import ProjectCreate, ProjectRead

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    try:
        path = validate_repo_path(body.repo_path)
    except SandboxError as e:
        raise HTTPException(400, str(e)) from e

    if db.query(Project).filter_by(name=body.name).first():
        raise HTTPException(409, f"project '{body.name}' already exists")

    p = Project(name=body.name, repo_path=str(path))
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.created_at.desc()).all()


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return p


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    db.delete(p)
    db.commit()


@router.post("/{project_id}/analyze")
async def analyze(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")

    result = await ArchitectAgent().run({"repo_path": p.repo_path, "errors": []})
    analysis = result["analysis"]

    p.analysis = analysis
    p.language = analysis["language"]
    p.framework = analysis["framework"]
    p.entrypoint = analysis["entrypoint"]
    p.exposed_port = analysis["exposed_port"]
    p.last_analyzed_at = datetime.now(UTC)
    db.commit()
    return analysis


@router.get("/{project_id}/graph")
def get_graph(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    if not p.analysis:
        raise HTTPException(400, "project has not been analyzed yet")
    return p.analysis.get("graph", {"nodes": [], "links": []})
