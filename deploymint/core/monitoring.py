"""Monitoring page data: fleet status + per-agent performance aggregation.
See docs/36-monitoring.md.

Nothing here is new instrumentation — agents/graph.py's _wrap() already
emits node.enter/node.exit (with ms) for every agent on every run, persisted
to the events table. This module only aggregates what already exists.
"""

import asyncio
from statistics import mean, median

from sqlalchemy.orm import Session

from deploymint.core import docker_run, kube_engine
from deploymint.db.models import Event, Project, Run

AGENTS = ["architect", "smith", "warden", "redteam", "code_audit", "execution", "oracle", "finops"]

# Fan-out cap on "Recheck now" — a fleet with more running deployments than
# this checks the first N only, never blocks the page indefinitely.
MAX_RECHECK = 25


def fleet_status(db: Session) -> list[dict]:
    """Every project's latest run + deployment snapshot — same N+1 pattern
    web/routes.py's dashboard() already uses for latest_runs."""
    projects = db.query(Project).order_by(Project.name).all()
    out = []
    for p in projects:
        latest = (db.query(Run).filter(Run.project_id == p.id)
                  .order_by(Run.created_at.desc()).first())
        dep = (latest.deployment or {}) if latest else {}
        out.append({
            "project": p,
            "run_id": latest.id if latest else None,
            "run_status": latest.status if latest else None,
            "deploy_status": dep.get("status"),
            "mode": dep.get("mode"),
            "local_url": dep.get("local_url"),
            "pod_name": dep.get("pod_name"),
            "container_id": dep.get("container_id"),
        })
    return out


async def recheck_health(fleet: list[dict]) -> dict[str, bool]:
    """On-demand live health re-check for every currently-"running" entry —
    a button, not a background poller (that's the separate drift-watcher
    scope). Reuses the single-target primitives execution.py already calls;
    this just fans them out concurrently across the fleet, capped."""
    candidates = [f for f in fleet if f["deploy_status"] == "running"][:MAX_RECHECK]

    async def check(entry: dict) -> tuple[str, bool]:
        try:
            if entry["mode"] == "kubernetes" and entry["pod_name"]:
                pod = await kube_engine.get_pod_name(entry["project"].name)
                return entry["run_id"], bool(pod)
            if entry["container_id"]:
                return entry["run_id"], await docker_run.container_healthy(entry["project"].name)
        except Exception:
            return entry["run_id"], False
        return entry["run_id"], False

    results = await asyncio.gather(*(check(e) for e in candidates))
    return dict(results)


def agent_performance(db: Session, run_limit: int = 50) -> list[dict]:
    """Per-agent aggregate stats across the last `run_limit` runs' node.exit
    events — avg/median/max ms, and an error rate from error-type events
    carrying the same node."""
    recent_run_ids = [
        r.id for r in db.query(Run.id).order_by(Run.created_at.desc()).limit(run_limit).all()
    ]
    if not recent_run_ids:
        return [{"agent": a, "runs": 0, "avg_ms": 0, "median_ms": 0, "max_ms": 0,
                 "errors": 0, "error_rate": 0.0} for a in AGENTS]

    exits = (
        db.query(Event)
        .filter(Event.run_id.in_(recent_run_ids), Event.type == "node.exit")
        .all()
    )
    errors = (
        db.query(Event)
        .filter(Event.run_id.in_(recent_run_ids), Event.type == "error")
        .all()
    )

    by_agent: dict[str, list[int]] = {a: [] for a in AGENTS}
    for e in exits:
        node = (e.payload or {}).get("node")
        ms = (e.payload or {}).get("ms")
        if node in by_agent and isinstance(ms, int | float):
            by_agent[node].append(int(ms))

    error_counts: dict[str, int] = dict.fromkeys(AGENTS, 0)
    for e in errors:
        node = (e.payload or {}).get("node")
        if node in error_counts:
            error_counts[node] += 1

    stats = []
    for agent in AGENTS:
        times = by_agent[agent]
        n = len(times)
        stats.append({
            "agent": agent,
            "runs": n,
            "avg_ms": round(mean(times)) if times else 0,
            "median_ms": round(median(times)) if times else 0,
            "max_ms": max(times) if times else 0,
            "errors": error_counts[agent],
            "error_rate": round(error_counts[agent] / n * 100, 1) if n else 0.0,
        })
    return stats


def run_cost_summary(db: Session, run_limit: int = 50) -> dict:
    """LLM cost broken out by model, across the last `run_limit` runs —
    schemas/run.py's _PRICING_PER_1M/llm_cost_usd already computes the
    per-run number; this just groups it by model_used."""
    from deploymint.schemas.run import _pricing_for

    runs = (
        db.query(Run.model_used, Run.input_tokens, Run.output_tokens, Run.created_at)
        .filter(Run.input_tokens.isnot(None))
        .order_by(Run.created_at.desc())
        .limit(run_limit)
        .all()
    )
    by_model: dict[str, float] = {}
    total = 0.0
    for model_used, input_tokens, output_tokens, _created in runs:
        input_rate, output_rate = _pricing_for(model_used)
        cost = (input_tokens or 0) * (input_rate / 1e6) + (output_tokens or 0) * (output_rate / 1e6)
        key = model_used or "unknown"
        by_model[key] = by_model.get(key, 0.0) + cost
        total += cost
    return {"by_model": {k: round(v, 4) for k, v in by_model.items()}, "total": round(total, 4),
           "run_count": len(runs)}
