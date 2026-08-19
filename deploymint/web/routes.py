"""Server-rendered pages: Jinja2 + HTMX + a vendored WebSocket client. No npm,
no build step. See docs/10-phase-6-finops-ui.md §6.4."""

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from deploymint.api.costs import _sample_export_by_service
from deploymint.core.artifact_store import FILENAMES as ARTIFACT_FILENAMES
from deploymint.core.naming import slugify
from deploymint.core.sandbox import SandboxError, list_workspace_dirs, validate_repo_path
from deploymint.db.database import get_db
from deploymint.db.models import Project, Run
from deploymint.runner.manager import start_run
from deploymint.web import docs_content

router = APIRouter()
WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

NODES = ["architect", "smith", "warden", "redteam", "execution", "oracle", "finops"]


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """The marketing landing page — no DB query, nothing functional. The
    register-a-project form and the project grid live at /dashboard. See
    docs/24-landing-and-docs.md."""
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    # One extra small query per project (dashboard-scale, not a hot path) —
    # gives each card a last-run status/time instead of just a bare name.
    latest_runs = {
        p.id: db.query(Run).filter(Run.project_id == p.id)
                 .order_by(Run.created_at.desc()).first()
        for p in projects
    }
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"projects": projects, "latest_runs": latest_runs,
         "workspace_dirs": list_workspace_dirs()})


@router.post("/projects/register")
def register_project_form(
    request: Request, name: str = Form(...), repo_path: str = Form(...),
    cloud_provider: str = Form("aws"), db: Session = Depends(get_db),
):
    # app.js's wireRegisterForm() submits via fetch (so a duplicate-name 409
    # can show a rename modal instead of a dead-end page reload — see
    # docs/27-rename-modal.md) and marks itself with this header. A plain
    # HTML form submission (no JS) still works exactly as before — same
    # validation, same redirect-on-success, just server-rendered errors
    # instead of a modal.
    is_ajax = request.headers.get("x-requested-with") == "fetch"
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    workspace_dirs = list_workspace_dirs()

    def fail(message: str, status_code: int):
        if is_ajax:
            return JSONResponse({"error": message}, status_code=status_code)
        return templates.TemplateResponse(
            request, "dashboard.html",
            {"projects": projects, "workspace_dirs": workspace_dirs, "error": message},
            status_code=status_code)

    try:
        path = validate_repo_path(repo_path)
    except SandboxError as e:
        return fail(str(e), 400)

    # Same sanitization the JSON API's ProjectCreate applies (core/naming.py)
    # — this form used to skip it entirely, so a name like "bew proj" would
    # sail into the DB and only blow up later, at the Execution stage, as an
    # "invalid reference format" Docker tag error. See docs/22-naming.md.
    try:
        name = slugify(name)
    except ValueError as e:
        return fail(str(e), 400)

    if db.query(Project).filter_by(name=name).first():
        return fail(f"project '{name}' already exists", 409)

    if cloud_provider not in ("aws", "gcp", "azure"):
        cloud_provider = "aws"

    p = Project(name=name, repo_path=str(path), cloud_provider=cloud_provider)
    db.add(p)
    db.commit()

    if is_ajax:
        return JSONResponse({"redirect": f"/projects/{p.id}"}, status_code=201)
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


def _fixable_files(run: Run) -> dict[str, str]:
    """Maps a finding's reported `file` to the real generated-artifact path
    it belongs to, for the findings that have one at all. Checkov reports a
    bare basename ("deploy.yml") while the artifact lives at a nested path
    (".github/workflows/deploy.yml"), so match on basename. Findings with no
    real file (Red Team's `file: "-"`) simply get no entry, and the template
    then shows no "Suggest a fix" button for them. See docs/28-ai-fix.md."""
    if not run.artifacts:
        return {}
    present = {
        fname for key, fname in ARTIFACT_FILENAMES.items() if run.artifacts.get(key)
    }
    by_basename = {Path(f).name: f for f in present}

    mapping: dict[str, str] = {}
    for finding in (run.security or {}).get("findings", []):
        reported = finding.get("file")
        if not reported or reported == "-":
            continue
        if reported in present:
            mapping[reported] = reported
        elif Path(reported).name in by_basename:
            mapping[reported] = by_basename[Path(reported).name]
    return mapping


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        return HTMLResponse("Run not found", status_code=404)
    project = db.get(Project, r.project_id)
    return templates.TemplateResponse(
        request, "run.html",
        {"run": r, "project": project, "project_name": project.name if project else "?",
         "nodes": NODES, "fixable_files": _fixable_files(r)})


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
