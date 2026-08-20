"""Server-rendered pages: Jinja2 + HTMX + a vendored WebSocket client. No npm,
no build step. See docs/10-phase-6-finops-ui.md §6.4."""

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from deploymint.api.costs import _sample_export_by_service
from deploymint.config import get_settings
from deploymint.core import monitoring
from deploymint.core.artifact_store import FILENAMES as ARTIFACT_FILENAMES
from deploymint.core.credential_store import credential_status
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

# A project with a run in one of these statuses is "locked" — the Deploy
# button stays disabled and a second POST is rejected server-side too. See
# docs/33-deploy-lock-and-findings.md. "awaiting_approval" is included even
# though the approval-gate feature (below) is what produces it, so no
# follow-up patch is needed once that ships.
ACTIVE_RUN_STATUSES = ("pending", "running", "awaiting_approval")


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
    # Deploy button lock: a project with a run still in flight shows a
    # disabled button + a link to that run instead of a second, silently
    # concurrent deploy. See docs/33-deploy-lock-and-findings.md.
    active_run = next((r for r in runs if r.status in ACTIVE_RUN_STATUSES), None)
    return templates.TemplateResponse(
        request, "project.html", {"project": p, "runs": runs, "active_run": active_run})


@router.post("/projects/{project_id}/deploy")
async def deploy_project_form(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        return HTMLResponse("Project not found", status_code=404)
    active = (db.query(Run).filter(Run.project_id == project_id,
                                   Run.status.in_(ACTIVE_RUN_STATUSES)).first())
    if active:
        return HTMLResponse(
            f"A run is already {active.status} for this project — see /runs/{active.id}",
            status_code=409)
    run_id = await start_run(
        p, trigger="web", stop_after_architect=get_settings().enable_approval_gate)
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


_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
_PROMINENT_SEVERITIES = ("critical", "high")


def _split_by_severity(run: Run) -> tuple[list[dict], list[dict], list[dict]]:
    """Splits a run's findings into (critical_high, your_deps, base_image) — a
    real run can return 200+ CVEs against base-image OS packages (glibc,
    util-linux, perl-base...) the user never chose and can't patch except by
    bumping the base image, alongside a handful of critical/high findings and
    a handful of CVEs in the project's own dependencies that ARE actionable
    (bump requirements.txt). Dumping all of them in one flat list buries the
    signal and reads as "your code is broken" when it's mostly inherited
    noise. All three lists are sorted critical-first. See
    docs/33-deploy-lock-and-findings.md."""
    findings = (run.security or {}).get("findings", [])
    order = {lvl: i for i, lvl in enumerate(_SEVERITY_ORDER)}
    findings = sorted(findings, key=lambda f: order.get(f.get("severity"), 99))
    critical_high = [f for f in findings if f.get("severity") in _PROMINENT_SEVERITIES]
    non_prominent = [f for f in findings if f.get("severity") not in _PROMINENT_SEVERITIES]
    # Checkov/OPA findings (no package_type — they scan generated files, not
    # packages) and Trivy library-type findings are both things the user can
    # actually fix in this repo; only Trivy os-pkgs findings are base-image
    # inherited noise.
    base_image = [f for f in non_prominent if f.get("package_type") == "os"]
    your_deps = [f for f in non_prominent if f.get("package_type") != "os"]
    return critical_high, your_deps, base_image


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        return HTMLResponse("Run not found", status_code=404)
    project = db.get(Project, r.project_id)
    critical_high, your_deps, base_image = _split_by_severity(r)
    return templates.TemplateResponse(
        request, "run.html",
        {"run": r, "project": project, "project_name": project.name if project else "?",
         "nodes": NODES, "fixable_files": _fixable_files(r),
         "critical_high_findings": critical_high, "your_deps_findings": your_deps,
         "base_image_findings": base_image})


@router.get("/costs", response_class=HTMLResponse)
def costs_page(request: Request):
    breakdown = _sample_export_by_service()
    return templates.TemplateResponse(request, "costs.html", {"breakdown": breakdown})


@router.get("/monitoring", response_class=HTMLResponse)
def monitoring_page(request: Request, db: Session = Depends(get_db)):
    """Global cloud credentials, fleet status, per-agent performance, and
    cost — see docs/36-monitoring.md."""
    return templates.TemplateResponse(
        request, "monitoring.html",
        {"credentials": credential_status(),
         "fleet": monitoring.fleet_status(db),
         "agent_stats": monitoring.agent_performance(db),
         "cost_summary": monitoring.run_cost_summary(db)})


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
