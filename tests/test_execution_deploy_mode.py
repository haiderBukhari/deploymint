"""ExecutionEngineAgent's deploy_mode gate — the approval-gate knob
(docs/33) that was collected in the UI but never read. See
docs/35-kind-cluster.md."""

from unittest.mock import AsyncMock, patch

import pytest

from deploymint.agents.execution import ExecutionEngineAgent
from deploymint.core.runner import CommandResult


def _ok(stdout=""):
    return CommandResult(argv=[], exit_code=0, stdout=stdout, stderr="")


class _AutoKindOff:
    enable_auto_kind_cluster = False


class _AutoKindOn:
    enable_auto_kind_cluster = True


def _base_state(**overrides):
    # ExecutionEngineAgent's AuditChain.record() writes to the audit_logs
    # table, which has a real FK to runs.id — a Run row must exist first.
    from deploymint.db.database import get_session_factory
    from deploymint.db.models import Project, Run

    run_id = overrides.get("run_id", "run_x")
    Session = get_session_factory()
    with Session() as db:
        if not db.get(Run, run_id):
            project = Project(name=f"proj-{run_id}", repo_path=overrides.get("repo_path", "/tmp"))
            db.add(project)
            db.commit()
            db.refresh(project)
            db.add(Run(id=run_id, project_id=project.id, status="running",
                       trigger="test", force=False, errors=[]))
            db.commit()

    base = {"run_id": run_id, "project_name": "proj", "repo_path": "/tmp/proj",
           "analysis": {"exposed_port": 8000}, "errors": []}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_deploy_mode_docker_skips_kubernetes_even_when_cluster_reachable(workspace):
    """Explicit user choice always wins — "docker run only" must actually do
    that, not just be collected and ignored."""
    state = _base_state(repo_path=str(workspace), approved_plan={"deploy_mode": "docker"})
    with patch("deploymint.core.docker_engine.build_image", new=AsyncMock(return_value="img")), \
         patch("deploymint.core.kube_engine.cluster_reachable", new=AsyncMock(return_value=True)), \
         patch("deploymint.core.docker_run.run_container", new=AsyncMock(return_value=_ok())), \
         patch("deploymint.core.docker_run.published_port", new=AsyncMock(return_value=8000)), \
         patch("deploymint.core.docker_run.container_healthy", new=AsyncMock(return_value=True)):
        out = await ExecutionEngineAgent().run(state)
    assert out["deployment"].get("mode") == "docker", out.get("errors")



@pytest.mark.asyncio
async def test_default_deploy_mode_still_uses_kubernetes_when_reachable(workspace):
    """No approved_plan at all (the ungated path) must behave exactly as
    before — reachable cluster means kubernetes, unchanged."""
    state = _base_state(repo_path=str(workspace))
    with patch("deploymint.core.docker_engine.build_image", new=AsyncMock(return_value="img")), \
         patch("deploymint.core.kube_engine.cluster_reachable", new=AsyncMock(return_value=True)), \
         patch("deploymint.core.kube_engine.kind_cluster_name", new=AsyncMock(return_value=None)), \
         patch("deploymint.core.kube_engine.apply", new=AsyncMock(return_value=_ok())), \
         patch("deploymint.core.kube_engine.rollout_status", new=AsyncMock(return_value=_ok())), \
         patch("deploymint.core.kube_engine.get_pod_name", new=AsyncMock(return_value="pod-x")):
        out = await ExecutionEngineAgent().run(state)
    assert out["deployment"]["mode"] == "kubernetes"


@pytest.mark.asyncio
async def test_auto_kind_disabled_by_default_never_calls_ensure_kind_cluster(workspace):
    """The default (enable_auto_kind_cluster=False) must be a complete no-op
    — today's behavior stays byte-for-byte identical for anyone not opting
    in."""
    state = _base_state(repo_path=str(workspace), approved_plan={"deploy_mode": "kubernetes"})
    with patch("deploymint.core.docker_engine.build_image", new=AsyncMock(return_value="img")), \
         patch("deploymint.core.kube_engine.cluster_reachable",
               new=AsyncMock(return_value=False)), \
         patch("deploymint.core.kube_engine.ensure_kind_cluster") as mock_ensure, \
         patch("deploymint.core.docker_run.run_container", new=AsyncMock(return_value=_ok())), \
         patch("deploymint.core.docker_run.published_port", new=AsyncMock(return_value=8000)), \
         patch("deploymint.core.docker_run.container_healthy", new=AsyncMock(return_value=True)), \
         patch("deploymint.agents.execution.get_settings", return_value=_AutoKindOff()):
        out = await ExecutionEngineAgent().run(state)
    mock_ensure.assert_not_called()
    assert out["deployment"]["mode"] == "docker"


@pytest.mark.asyncio
async def test_auto_kind_enabled_tries_to_provision_when_unreachable(workspace):
    state = _base_state(repo_path=str(workspace), approved_plan={"deploy_mode": "kubernetes"})
    reachable_calls = AsyncMock(side_effect=[False, True])
    with patch("deploymint.core.docker_engine.build_image", new=AsyncMock(return_value="img")), \
         patch("deploymint.core.kube_engine.cluster_reachable", new=reachable_calls), \
         patch("deploymint.core.kube_engine.ensure_kind_cluster",
               new=AsyncMock(return_value=True)) as mock_ensure, \
         patch("deploymint.core.kube_engine.kind_cluster_name", new=AsyncMock(return_value=None)), \
         patch("deploymint.core.kube_engine.apply", new=AsyncMock(return_value=_ok())), \
         patch("deploymint.core.kube_engine.rollout_status", new=AsyncMock(return_value=_ok())), \
         patch("deploymint.core.kube_engine.get_pod_name", new=AsyncMock(return_value="pod-x")), \
         patch("deploymint.agents.execution.get_settings", return_value=_AutoKindOn()):
        out = await ExecutionEngineAgent().run(state)
    mock_ensure.assert_called_once()
    assert out["deployment"]["mode"] == "kubernetes"


@pytest.mark.asyncio
async def test_auto_kind_failure_falls_through_to_docker_without_raising(workspace):
    state = _base_state(repo_path=str(workspace), approved_plan={"deploy_mode": "kubernetes"})
    with patch("deploymint.core.docker_engine.build_image", new=AsyncMock(return_value="img")), \
         patch("deploymint.core.kube_engine.cluster_reachable",
               new=AsyncMock(return_value=False)), \
         patch("deploymint.core.kube_engine.ensure_kind_cluster",
               new=AsyncMock(return_value=False)), \
         patch("deploymint.core.docker_run.run_container", new=AsyncMock(return_value=_ok())), \
         patch("deploymint.core.docker_run.published_port", new=AsyncMock(return_value=8000)), \
         patch("deploymint.core.docker_run.container_healthy", new=AsyncMock(return_value=True)), \
         patch("deploymint.agents.execution.get_settings", return_value=_AutoKindOn()):
        out = await ExecutionEngineAgent().run(state)
    assert out["deployment"]["mode"] == "docker"
    assert out["deployment"]["status"] == "running"
