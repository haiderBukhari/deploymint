"""Oracle: deterministic restart/crashloop/ready checks drive the rollback
decision, not the optional IsolationForest pass. All kubectl calls are mocked
here — the real cluster path is covered separately by the live-crashloop
smoke test. See docs/10-phase-6-finops-ui.md §6.1-6.2."""

from unittest.mock import AsyncMock, patch

import pytest

from deploymint.agents.oracle import ObservabilityOracleAgent


def _cmd_result(stdout="", ok=True):
    from deploymint.core.runner import CommandResult

    return CommandResult(argv=[], exit_code=0 if ok else 1, stdout=stdout, stderr="")


@pytest.mark.asyncio
async def test_non_kubernetes_deployment_is_a_noop():
    out = await ObservabilityOracleAgent().run(
        {"deployment": {"status": "running", "mode": "docker"}, "project_name": "t"}
    )
    assert out["deployment"]["status"] == "running"


@pytest.mark.asyncio
async def test_not_running_deployment_is_skipped_entirely():
    out = await ObservabilityOracleAgent().run(
        {"deployment": {"status": "failed", "mode": "kubernetes"}, "project_name": "t"}
    )
    assert out == {}


@pytest.mark.asyncio
async def test_crashloop_on_first_sample_triggers_immediate_rollback(monkeypatch):
    from deploymint.config import get_settings

    monkeypatch.setenv("DEPLOYMINT_ORACLE_SAMPLES", "12")
    monkeypatch.setenv("DEPLOYMINT_ORACLE_INTERVAL", "0")
    get_settings.cache_clear()

    pods_json = (
        '{"items": [{"status": {"containerStatuses": [{"restartCount": 5, '
        '"ready": false, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}}]}'
    )

    async def fake_run_command(argv, **kw):
        if "top" in argv:
            return _cmd_result(ok=False)
        return _cmd_result(stdout=pods_json)

    with patch("deploymint.agents.oracle.run_command", side_effect=fake_run_command), \
         patch("deploymint.agents.remediator.kube_engine.rollout_undo",
               new=AsyncMock(return_value=_cmd_result(ok=True))), \
         patch("deploymint.agents.remediator.kube_engine.rollout_status",
               new=AsyncMock(return_value=_cmd_result(ok=True))):
        out = await ObservabilityOracleAgent().run(
            {"deployment": {"status": "running", "mode": "kubernetes"},
             "project_name": "t", "errors": []}
        )

    get_settings.cache_clear()
    dep = out["deployment"]
    assert dep["status"] == "rolled_back"
    assert "CrashLoopBackOff" in dep["remediation"]
    assert any("oracle:" in e for e in out["errors"])


@pytest.mark.asyncio
async def test_healthy_pod_reports_done_with_no_rollback(monkeypatch):
    from deploymint.config import get_settings

    monkeypatch.setenv("DEPLOYMINT_ORACLE_SAMPLES", "2")
    monkeypatch.setenv("DEPLOYMINT_ORACLE_INTERVAL", "0")
    get_settings.cache_clear()

    healthy_json = (
        '{"items": [{"status": {"containerStatuses": [{"restartCount": 0, '
        '"ready": true, "state": {}}]}}]}'
    )

    async def fake_run_command(argv, **kw):
        if "top" in argv:
            return _cmd_result(ok=False)
        return _cmd_result(stdout=healthy_json)

    with patch("deploymint.agents.oracle.run_command", side_effect=fake_run_command):
        out = await ObservabilityOracleAgent().run(
            {"deployment": {"status": "running", "mode": "kubernetes"},
             "project_name": "t", "errors": []}
        )

    get_settings.cache_clear()
    assert out["deployment"]["status"] == "running"
    assert "metrics" in out["deployment"]
