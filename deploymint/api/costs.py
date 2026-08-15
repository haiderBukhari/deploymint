"""Cost Q&A. The LLM classifies intent and (optionally) phrases the answer —
it never computes a number. Every figure in `data` is deterministic: a live
AWS Cost Explorer connection when credentials are present, the sample export
otherwise, plus local run estimates layered on top either way.
See docs/10-phase-6-finops-ui.md §6.3 and docs/17-pending-work.md §17.5."""

import json
from importlib.resources import files

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from deploymint.core import aws_cost
from deploymint.db.database import get_db
from deploymint.db.models import Run

router = APIRouter(prefix="/api/costs", tags=["costs"])

COST_INTENTS = {
    "most_expensive": ["most expensive", "costs the most", "biggest cost", "highest"],
    "total_spend":    ["total", "how much", "overall", "altogether"],
    "by_service":     ["breakdown", "by service", "per service", "each"],
    "optimize":       ["save", "reduce", "optimize", "cheaper", "cut"],
}


def classify_cost_intent(question: str) -> str:
    low = question.lower()
    for intent, phrases in COST_INTENTS.items():
        if any(p in low for p in phrases):
            return intent
    return "total_spend"


def _sample_export_by_service() -> dict[str, float]:
    data = json.loads((files("deploymint") / "data/sample_cost_export.json").read_text())
    groups = data["ResultsByTime"][0]["Groups"]
    return {g["Keys"][0]: float(g["Metrics"]["UnblendedCost"]["Amount"]) for g in groups}


def load_cost_data(db: Session) -> dict:
    """Live AWS Cost Explorer when credentials are present, the bundled sample
    export otherwise — see core/aws_cost.py for why this never hard-fails."""
    live = aws_cost.fetch_cost_by_service()
    if live:
        by_service, period = live
        source = "aws_ce"
    else:
        by_service = _sample_export_by_service()
        period = "2026-07-01 to 2026-08-01"
        source = "sample_json"

    local_estimates = (
        db.query(Run).filter(Run.cost.isnot(None)).order_by(Run.created_at.desc()).limit(20).all()
    )
    if local_estimates:
        by_service = dict(by_service)
        for r in local_estimates:
            monthly = (r.cost or {}).get("monthly_usd")
            if monthly:
                by_service[f"DeployMint run {r.id}"] = monthly

    return {"by_service": by_service, "period": period, "source": source}


@router.post("/query")
async def query_costs(body: dict, db: Session = Depends(get_db)):
    question = (body.get("question") or "").strip()
    data = load_cost_data(db)
    intent = classify_cost_intent(question)

    if not data["by_service"]:
        return {"answer": "No cost data available yet.", "intent": intent, "data": {},
                "source": data["source"]}

    total = sum(data["by_service"].values())

    if intent == "most_expensive":
        svc, amount = max(data["by_service"].items(), key=lambda kv: kv[1])
        share = amount / total * 100 if total else 0
        facts = {"service": svc, "amount": round(amount, 2), "share_pct": round(share, 1),
                 "period": data["period"]}
        answer = (f"**{svc}** is your largest cost at **${amount:,.2f}** "
                  f"for {data['period']} — {share:.0f}% of total spend.")
    elif intent == "by_service":
        facts = {"by_service": {k: round(v, 2) for k, v in data["by_service"].items()},
                 "period": data["period"]}
        lines = "\n".join(f"- {k}: ${v:,.2f}" for k, v in
                          sorted(data["by_service"].items(), key=lambda kv: -kv[1]))
        answer = f"Cost breakdown for {data['period']}:\n{lines}"
    elif intent == "optimize":
        svc, amount = max(data["by_service"].items(), key=lambda kv: kv[1])
        facts = {"service": svc, "amount": round(amount, 2), "period": data["period"]}
        answer = (f"Your biggest lever is **{svc}** at ${amount:,.2f}/mo. "
                  "Check recent runs' FinOps recommendations for concrete cuts "
                  "(unbounded limits, oversized requests, no autoscaling).")
    else:
        facts = {"total": round(total, 2), "period": data["period"]}
        answer = f"Total spend for {data['period']} was **${total:,.2f}**."

    return {"answer": answer, "intent": intent, "data": facts, "source": data["source"]}
