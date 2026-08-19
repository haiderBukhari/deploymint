"""Regression test for a real bug: a redeploy of the same project reuses the
same container name, and `run_container` fired `docker rm -f` without ever
checking whether it actually worked — a race with the old container still
stopping produced Docker's own "Conflict... already in use" error on the
following `docker run`. See docs/26-redeploy-conflict-fix.md.

Mocks core.runner.run_command entirely — no real Docker daemon needed."""

from unittest.mock import AsyncMock

import pytest

from deploymint.core import docker_run
from deploymint.core.runner import CommandResult


def _result(argv, exit_code=0, stdout="", stderr=""):
    return CommandResult(argv=argv, exit_code=exit_code, stdout=stdout, stderr=stderr)


@pytest.mark.asyncio
async def test_run_container_succeeds_on_the_first_try(monkeypatch):
    calls = []

    async def fake_run_command(argv, **kw):
        calls.append(argv)
        return _result(argv, exit_code=0)

    monkeypatch.setattr(docker_run, "run_command", fake_run_command)
    r = await docker_run.run_container("my-app", "my-app:latest", 8000)

    assert r.ok
    assert calls[0][:3] == ["docker", "rm", "-f"]
    assert calls[1][:2] == ["docker", "run"]
    assert len(calls) == 2  # no retry needed


@pytest.mark.asyncio
async def test_run_container_retries_once_on_a_name_conflict(monkeypatch):
    calls = []

    async def fake_run_command(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "run"] and len(calls) == 2:
            return _result(argv, exit_code=1, stderr=(
                'docker: Error response from daemon: Conflict. The container '
                'name "/my-app" is already in use by container "abc123". '
                "You have to remove (or rename) that container to be able to "
                "reuse that name."))
        return _result(argv, exit_code=0)

    monkeypatch.setattr(docker_run, "run_command", fake_run_command)
    r = await docker_run.run_container("my-app", "my-app:latest", 8000)

    assert r.ok
    # rm, run(conflict), rm again, run again
    assert [c[:3] if c[:2] == ["docker", "rm"] else c[:2] for c in calls] == [
        ["docker", "rm", "-f"], ["docker", "run"], ["docker", "rm", "-f"], ["docker", "run"],
    ]


@pytest.mark.asyncio
async def test_run_container_gives_up_after_one_retry(monkeypatch):
    async def fake_run_command(argv, **kw):
        if argv[:2] == ["docker", "run"]:
            return _result(argv, exit_code=1, stderr="Conflict. already in use")
        return _result(argv, exit_code=0)

    monkeypatch.setattr(docker_run, "run_command", fake_run_command)
    r = await docker_run.run_container("my-app", "my-app:latest", 8000)

    assert not r.ok  # still fails after the one retry — doesn't loop forever


@pytest.mark.asyncio
async def test_run_container_does_not_retry_on_an_unrelated_failure(monkeypatch):
    calls = []

    async def fake_run_command(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            return _result(argv, exit_code=1, stderr="Error: no such image: my-app:latest")
        return _result(argv, exit_code=0)

    monkeypatch.setattr(docker_run, "run_command", fake_run_command)
    r = await docker_run.run_container("my-app", "my-app:latest", 8000)

    assert not r.ok
    assert len(calls) == 2  # one rm, one run — no wasted retry on an unrelated error


@pytest.mark.asyncio
async def test_run_container_forwards_kw_so_output_is_actually_logged(monkeypatch):
    """The old code called the cleanup `docker rm -f` with no **kw at all —
    its output never reached the terminal/audit log. Both calls now forward
    whatever the caller passed (recorder/audit/on_line)."""
    seen_kwargs = []

    async def fake_run_command(argv, **kw):
        seen_kwargs.append(kw)
        return _result(argv, exit_code=0)

    monkeypatch.setattr(docker_run, "run_command", fake_run_command)
    marker = AsyncMock()
    await docker_run.run_container("my-app", "my-app:latest", 8000, on_line=marker)

    assert all(kw.get("on_line") is marker for kw in seen_kwargs)
