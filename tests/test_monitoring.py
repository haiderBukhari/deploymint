"""Monitoring page aggregation — fleet status, per-agent performance, cost.
See docs/36-monitoring.md."""

from unittest.mock import AsyncMock, patch

import pytest

from deploymint.core import monitoring
from deploymint.db.database import get_session_factory
from deploymint.db.models import Event, Project, Run


def _make_project(name="proj-a", repo_path="/tmp/x"):
    Session = get_session_factory()
    with Session() as db:
        p = Project(name=name, repo_path=repo_path)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id


def _make_run(project_id, run_id, status="success", deployment=None):
    Session = get_session_factory()
    with Session() as db:
        db.add(Run(id=run_id, project_id=project_id, status=status, trigger="test",
                   force=False, errors=[], deployment=deployment or {}))
        db.commit()


def _emit_event(run_id, seq, type_, payload):
    Session = get_session_factory()
    with Session() as db:
        db.add(Event(run_id=run_id, seq=seq, type=type_, payload=payload))
        db.commit()


def test_fleet_status_lists_every_project_with_latest_run(workspace):
    pid = _make_project()
    _make_run(pid, "run_1", status="success", deployment={"status": "running", "mode": "docker"})

    Session = get_session_factory()
    with Session() as db:
        fleet = monitoring.fleet_status(db)

    assert len(fleet) == 1
    assert fleet[0]["run_id"] == "run_1"
    assert fleet[0]["deploy_status"] == "running"
    assert fleet[0]["mode"] == "docker"


def test_fleet_status_handles_project_with_no_runs(workspace):
    _make_project()
    Session = get_session_factory()
    with Session() as db:
        fleet = monitoring.fleet_status(db)
    assert fleet[0]["run_id"] is None
    assert fleet[0]["deploy_status"] is None


@pytest.mark.asyncio
async def test_recheck_health_only_checks_running_deployments(workspace):
    fleet = [
        {"project": type("P", (), {"name": "a"})(), "run_id": "run_a",
         "deploy_status": "running", "mode": "docker", "container_id": "abc", "pod_name": None},
        {"project": type("P", (), {"name": "b"})(), "run_id": "run_b",
         "deploy_status": "failed", "mode": "docker", "container_id": "def", "pod_name": None},
    ]
    with patch("deploymint.core.docker_run.container_healthy", new=AsyncMock(return_value=True)):
        results = await monitoring.recheck_health(fleet)
    assert results == {"run_a": True}


def test_agent_performance_aggregates_node_exit_events(workspace):
    pid = _make_project()
    _make_run(pid, "run_1")
    _emit_event("run_1", 1, "node.exit", {"node": "architect", "ms": 100})
    _emit_event("run_1", 2, "node.exit", {"node": "architect", "ms": 200})
    _emit_event("run_1", 3, "node.exit", {"node": "smith", "ms": 500})
    _emit_event("run_1", 4, "error", {"node": "smith", "message": "boom"})

    Session = get_session_factory()
    with Session() as db:
        stats = monitoring.agent_performance(db)

    by_agent = {s["agent"]: s for s in stats}
    assert by_agent["architect"]["runs"] == 2
    assert by_agent["architect"]["avg_ms"] == 150
    assert by_agent["smith"]["runs"] == 1
    assert by_agent["smith"]["errors"] == 1
    assert by_agent["smith"]["error_rate"] == 100.0
    assert by_agent["oracle"]["runs"] == 0


def test_agent_performance_with_no_runs_returns_zeroed_stats(workspace):
    Session = get_session_factory()
    with Session() as db:
        stats = monitoring.agent_performance(db)
    assert all(s["runs"] == 0 for s in stats)
    assert {s["agent"] for s in stats} == set(monitoring.AGENTS)


def test_run_cost_summary_groups_by_model(workspace):
    Session = get_session_factory()
    with Session() as db:
        db.add(Run(id="run_1", project_id=_make_project("p2", "/tmp/y"), status="success",
                   trigger="test", force=False, errors=[], model_used="claude-opus-5",
                   input_tokens=1_000_000, output_tokens=1_000_000))
        db.commit()

    with Session() as db:
        summary = monitoring.run_cost_summary(db)

    assert summary["by_model"]["claude-opus-5"] == 30.0  # 5 + 25 per the pricing table
    assert summary["total"] == 30.0
    assert summary["run_count"] == 1
