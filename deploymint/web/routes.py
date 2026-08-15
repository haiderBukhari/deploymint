"""Server-rendered pages: Jinja2 + HTMX + a vendored WebSocket client. No npm,
no build step. See docs/10-phase-6-finops-ui.md §6.4."""

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from deploymint.api.costs import _sample_export_by_service
from deploymint.core.sandbox import SandboxError, validate_repo_path
from deploymint.db.database import get_db
from deploymint.db.models import Project, Run
from deploymint.runner.manager import start_run
from deploymint.web import docs_content

router = APIRouter()
WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

NODES = ["architect", "smith", "warden", "redteam", "execution", "oracle", "finops"]


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return templates.TemplateResponse(
        request, "index.html", {"projects": projects})


@router.post("/projects/register")
def register_project_form(
    request: Request, name: str = Form(...), repo_path: str = Form(...),
    cloud_provider: str = Form("aws"), db: Session = Depends(get_db),
):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    try:
        path = validate_repo_path(repo_path)
    except SandboxError as e:
        return templates.TemplateResponse(
            request, "index.html", {"projects": projects, "error": str(e)}, status_code=400)

    if db.query(Project).filter_by(name=name).first():
        return templates.TemplateResponse(
            request, "index.html",
            {"projects": projects, "error": f"project '{name}' already exists"},
            status_code=409)

    if cloud_provider not in ("aws", "gcp", "azure"):
        cloud_provider = "aws"

    p = Project(name=name, repo_path=str(path), cloud_provider=cloud_provider)
    db.add(p)
    db.commit()
    return RedirectResponse(f"/projects/{p.id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        return HTMLResponse("Project not found", status_code=404)
    runs = (db.query(Run).filter(Run.project_id == project_id)
            .order_by(Run.created_at.desc()).limit(20).all())
    return templates.TemplateResponse(
        request, "project.html", {"project": p, "runs": runs})


@router.post("/projects/{project_id}/deploy")
async def deploy_project_form(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        return HTMLResponse("Project not found", status_code=404)
    run_id = await start_run(p, trigger="web")
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        return HTMLResponse("Run not found", status_code=404)
    project = db.get(Project, r.project_id)
    return templates.TemplateResponse(
        request, "run.html",
        {"run": r, "project_name": project.name if project else "?", "nodes": NODES})


@router.get("/costs", response_class=HTMLResponse)
def costs_page(request: Request):
    breakdown = _sample_export_by_service()
    return templates.TemplateResponse(request, "costs.html", {"breakdown": breakdown})


# NOTE: this is intentionally /guide, not /docs — FastAPI's own /docs is its
# built-in interactive Swagger UI (see deploymint/server.py's default
# docs_url), and this route would silently never register if it collided
# with that path. Found by writing a test that actually hit the route.
@router.get("/guide", response_class=HTMLResponse)
def docs_index(request: Request):
    first = docs_content.NAV[0]
    return docs_page(request, first.slug)


@router.get("/guide/{slug}", response_class=HTMLResponse)
def docs_page(request: Request, slug: str):
    page = docs_content.get_page(slug)
    if not page:
        return HTMLResponse("Doc page not found", status_code=404)
    html = docs_content.render(page)
    return templates.TemplateResponse(
        request, "docs.html",
        {"nav": docs_content.NAV, "page": page, "content": html})
