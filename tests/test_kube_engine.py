"""kube_engine.ensure_kind_cluster() — real local Kubernetes cluster
auto-provisioning, opt-in. See docs/35-kind-cluster.md."""

from unittest.mock import AsyncMock

import pytest

from deploymint.core import kube_engine
from deploymint.core.runner import CommandResult


def _result(argv, exit_code=0, stdout="", stderr=""):
    return CommandResult(argv=argv, exit_code=exit_code, stdout=stdout, stderr=stderr)


@pytest.mark.asyncio
async def test_kind_cluster_exists_true_when_named_cluster_listed(monkeypatch):
    monkeypatch.setattr(
        kube_engine, "run_command",
        AsyncMock(return_value=_result([], stdout="deploymint\nother\n")))
    assert await kube_engine.kind_cluster_exists("deploymint") is True


@pytest.mark.asyncio
async def test_kind_cluster_exists_false_when_absent(monkeypatch):
    monkeypatch.setattr(
        kube_engine, "run_command",
        AsyncMock(return_value=_result([], stdout="other\n")))
    assert await kube_engine.kind_cluster_exists("deploymint") is False


@pytest.mark.asyncio
async def test_ensure_kind_cluster_returns_true_without_creating_when_already_exists(monkeypatch):
    calls = []

    async def fake_run_command(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["kind", "get"]:
            return _result(argv, stdout="deploymint\n")
        return _result(argv)  # would be the create call, if reached

    monkeypatch.setattr(kube_engine, "run_command", fake_run_command)
    result = await kube_engine.ensure_kind_cluster("deploymint")
    assert result is True
    assert not any(argv[:3] == ["kind", "create", "cluster"] for argv in calls)


@pytest.mark.asyncio
async def test_ensure_kind_cluster_creates_when_absent(monkeypatch):
    calls = []

    async def fake_run_command(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["kind", "get"]:
            return _result(argv, stdout="")
        if argv[:3] == ["kind", "create", "cluster"]:
            return _result(argv, exit_code=0)
        return _result(argv)

    monkeypatch.setattr(kube_engine, "run_command", fake_run_command)
    result = await kube_engine.ensure_kind_cluster("deploymint")
    assert result is True
    assert any(argv[:3] == ["kind", "create", "cluster"] for argv in calls)


@pytest.mark.asyncio
async def test_ensure_kind_cluster_returns_false_never_raises_on_create_failure(monkeypatch):
    async def fake_run_command(argv, **kw):
        if argv[:2] == ["kind", "get"]:
            return _result(argv, stdout="")
        return _result(argv, exit_code=1, stderr="boom")

    monkeypatch.setattr(kube_engine, "run_command", fake_run_command)
    result = await kube_engine.ensure_kind_cluster("deploymint")
    assert result is False


@pytest.mark.asyncio
async def test_ensure_kind_cluster_returns_false_never_raises_when_binary_missing(monkeypatch):
    async def fake_run_command(argv, **kw):
        raise FileNotFoundError("kind: command not found")

    monkeypatch.setattr(kube_engine, "run_command", fake_run_command)
    result = await kube_engine.ensure_kind_cluster("deploymint")
    assert result is False
