"""Natural language router. Classifies a chat message into an intent (via
Claude, with a deterministic keyword fallback) and routes it to the same
run pipeline everything else uses. See docs/09-phase-5-orchestration.md §5.4."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from deploymint.core import llm, prompts
from deploymint.db.database import get_db
from deploymint.db.models import Project
from deploymint.runner.manager import start_run

router = APIRouter(prefix="/api/chat", tags=["chat"])

KEYWORDS = {
    "deploy":   ["deploy", "ship", "launch", "release", "push live", " up "],
    "analyze":  ["analyze", "scan", "inspect", "understand", "graph", "look at"],
    "status":   ["status", "how is", "running", "health", "what's up"],
    "cost":     ["cost", "spend", "bill", "expensive", "price", "budget", "$"],
    "rollback": ["rollback", "revert", "undo", "back out"],
}

# Intents whose supporting agent doesn't exist yet (Oracle/FinOps land in
# Phase 6, rollback needs a deployed run's kube_engine.rollout_undo wired up
# to a chat action). Answer honestly rather than half-implementing.
NOT_YET_AVAILABLE = {
    "status": "Live run status isn't wired up to chat yet — check the run's page directly.",
    "cost": "Cost estimation lands in Phase 6 (FinOps agent) — not available yet.",
    "rollback": "Rollback via chat lands alongside the Phase 6 Oracle agent — not available yet.",
}


def keyword_intent(msg: str) -> tuple[str, float]:
    low = f" {msg.lower()} "
    for intent, words in KEYWORDS.items():
        if any(w in low for w in words):
            return intent, 0.6
    return "unknown", 0.0


async def classify(msg: str) -> dict:
    try:
        data = await llm.complete_json(prompts.INTENT_SYSTEM, msg)
        if data.get("intent") in {*KEYWORDS, "explain", "help", "unknown"}:
            return data
    except Exception:
        pass
    intent, conf = keyword_intent(msg)
    return {"intent": intent, "project": None, "params": {}, "confidence": conf}


def _project_list(db: Session) -> str:
    names = [p.name for p in db.query(Project).order_by(Project.name).all()]
    return f"Known projects: {', '.join(names)}." if names else "No projects registered yet."


def _resolve_project(db: Session, name: str | None, project_id: int | None) -> Project | None:
    if project_id is not None:
        return db.get(Project, project_id)
    if name:
        return db.query(Project).filter(Project.name == name).first()
    return None


@router.post("")
async def chat(body: dict, db: Session = Depends(get_db)):
    msg = (body.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "message is required")

    parsed = await classify(msg)
    intent = parsed["intent"]
    project = _resolve_project(db, parsed.get("project"), body.get("project_id"))

    if intent == "deploy":
        if not project:
            return {"intent": intent,
                    "reply": "Which project should I deploy? " + _project_list(db),
                    "action_taken": "none", "run_id": None}
        if parsed.get("confidence", 0) < 0.8:
            return {"intent": intent,
                    "reply": f"I think you want to deploy **{project.name}**. Confirm?",
                    "action_taken": "confirm_required", "run_id": None,
                    "pending": {"intent": "deploy", "project_id": project.id}}
        run_id = await start_run(project, trigger="chat",
                                 force=bool(parsed.get("params", {}).get("force")))
        return {"intent": intent,
                "reply": f"Deploying **{project.name}**. Watch it live: /runs/{run_id}",
                "action_taken": "run_started", "run_id": run_id}

    if intent in NOT_YET_AVAILABLE:
        return {"intent": intent, "reply": NOT_YET_AVAILABLE[intent],
                "action_taken": "none", "run_id": None}

    if intent == "analyze":
        if not project:
            return {"intent": intent,
                    "reply": "Which project should I analyze? " + _project_list(db),
                    "action_taken": "none", "run_id": None}
        return {"intent": intent,
                "reply": f"**{project.name}** — {project.language or 'unknown language'}, "
                         f"{project.framework or 'unknown framework'}. "
                         "Trigger a full re-analysis via POST /api/projects/{id}/analyze.",
                "action_taken": "none", "run_id": None}

    if intent == "help":
        return {"intent": intent,
                "reply": "I can deploy a registered project — try \"deploy <project name>\".",
                "action_taken": "none", "run_id": None}

    return {"intent": "unknown",
            "reply": "I didn't understand that. Try \"deploy <project name>\".",
            "action_taken": "none", "run_id": None}
