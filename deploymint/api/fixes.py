"""AI-suggested fixes for a security finding, and applying one. See
docs/28-ai-fix.md.

Applying a fix deliberately does NOT mutate the original run. This product's
whole pitch includes a tamper-evident audit trail — letting an already-
verified run's artifacts change after the fact would undermine exactly that.
Instead a new run row is created carrying the patched artifact set, and the
security gate is re-run against it, so both the before and after states stay
independently inspectable.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from deploymint.agents.warden import SecurityWardenAgent
from deploymint.core.artifact_store import FILENAMES
from deploymint.core.fix_suggester import suggest_fix
from deploymint.db.database import get_db
from deploymint.db.models import Project, Run
from deploymint.runner.manager import new_run_id

router = APIRouter(tags=["fixes"])

# filename-on-disk -> the artifacts dict key holding its content
_KEY_BY_FILENAME = {v: k for k, v in FILENAMES.items()}


class SuggestFixRequest(BaseModel):
    file: str
    finding_id: str


class ApplyFixRequest(BaseModel):
    file: str
    patched_content: str


def _resolve(run_id: str, filename: str, db: Session) -> tuple[Run, str, str]:
    """Returns (run, artifacts-dict key, current content) or raises."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    key = _KEY_BY_FILENAME.get(filename)
    if not key:
        raise HTTPException(400, f"not a generated artifact: {filename}")
    if not run.artifacts or not run.artifacts.get(key):
        raise HTTPException(400, f"this run has no {filename}")
    return run, key, run.artifacts[key]


@router.post("/api/runs/{run_id}/findings/suggest-fix")
async def suggest_finding_fix(run_id: str, body: SuggestFixRequest,
                              db: Session = Depends(get_db)):
    """Read-only — nothing is written anywhere by this route."""
    run, _key, content = _resolve(run_id, body.file, db)

    finding = next(
        (f for f in (run.security or {}).get("findings", [])
         if f.get("id") == body.finding_id and f.get("file") in (body.file, None)),
        None,
    )
    if finding is None:
        # Checkov reports a bare filename ("deploy.yml") while the artifact
        # path is nested (".github/workflows/deploy.yml") — fall back to
        # matching on the finding id alone rather than refusing the request.
        finding = next(
            (f for f in (run.security or {}).get("findings", [])
             if f.get("id") == body.finding_id), None)
    if finding is None:
        raise HTTPException(404, f"no finding {body.finding_id} on this run")

    try:
        return await suggest_fix(finding, content, body.file)
    except Exception as e:
        raise HTTPException(502, f"could not get a suggestion: {str(e)[:200]}") from e


@router.post("/api/runs/{run_id}/findings/apply-fix", status_code=201)
async def apply_finding_fix(run_id: str, body: ApplyFixRequest,
                            db: Session = Depends(get_db)):
    """Creates a NEW run carrying the patched artifact and re-runs only the
    security gate against it — not the full pipeline, since re-verifying the
    fix is the point, not redeploying anything."""
    run, key, _content = _resolve(run_id, body.file, db)
    project = db.get(Project, run.project_id)
    if not project:
        raise HTTPException(404, "project not found")

    patched_artifacts = {**run.artifacts, key: body.patched_content}

    new_id = new_run_id()
    new_run = Run(
        id=new_id, project_id=project.id, status="running", trigger="fix",
        current_node="warden", analysis=run.analysis, artifacts=patched_artifacts,
        errors=[], model_used=run.model_used,
    )
    db.add(new_run)
    db.commit()

    warden = SecurityWardenAgent()  # no event bus — this isn't a streamed run
    result = await warden.run({
        "run_id": new_id, "repo_path": project.repo_path,
        "artifacts": patched_artifacts, "errors": [],
    })
    security = result.get("security", {})

    new_run.security = security
    new_run.status = "success" if security.get("passed") else "blocked"
    new_run.current_node = "warden"
    new_run.completed_at = datetime.now(UTC)
    db.commit()

    return {
        "run_id": new_id,
        "status": new_run.status,
        "passed": bool(security.get("passed")),
        "findings": security.get("findings", []),
        "blocked_reason": security.get("blocked_reason"),
    }
