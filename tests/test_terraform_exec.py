"""See docs/21-cloud-deploy.md. Every test here mocks the terraform binary —
no test in this suite should ever need a real cloud account or the terraform
CLI installed to pass."""

import asyncio
from pathlib import Path

import pytest

from deploymint.core import terraform_exec
from deploymint.core.cloud_creds import CloudCredentials


async def noop(_line: str) -> None:
    pass


class _AsyncLineIterator:
    def __init__(self, lines: list[bytes]):
        self._iter = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as e:
            raise StopAsyncIteration from e


class FakeProcess:
    def __init__(self, lines: list[bytes], returncode: int = 0):
        self.stdout = _AsyncLineIterator(lines)
        self._returncode = returncode

    async def wait(self):
        return self._returncode


@pytest.fixture
def terraform_dir(tmp_path):
    d = tmp_path / "terraform"
    d.mkdir()
    (d / "main.tf").write_text('resource "null_resource" "x" {}')
    return d


@pytest.fixture
def aws_creds():
    return CloudCredentials(aws_access_key_id="AKIA", aws_secret_access_key="secret",
                             aws_region="us-east-1")


def test_terraform_available_checks_path(monkeypatch):
    monkeypatch.setattr(terraform_exec.shutil, "which", lambda name: "/usr/local/bin/terraform")
    assert terraform_exec.terraform_available()
    monkeypatch.setattr(terraform_exec.shutil, "which", lambda name: None)
    assert not terraform_exec.terraform_available()


@pytest.mark.asyncio
async def test_plan_runs_init_then_plan(monkeypatch, terraform_dir, aws_creds):
    monkeypatch.setattr(terraform_exec, "terraform_available", lambda: True)
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProcess([b"ok\n"], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    lines = []

    async def on_line(line):
        lines.append(line)

    ok = await terraform_exec.run_terraform("plan", terraform_dir, "aws", aws_creds, on_line)

    assert ok is True
    assert calls[0][:2] == ("terraform", "init")
    assert calls[1][:2] == ("terraform", "plan")
    assert any("completed successfully" in line for line in lines)


@pytest.mark.asyncio
async def test_apply_uses_saved_plan_file_when_present(monkeypatch, terraform_dir, aws_creds):
    (terraform_dir / "tfplan").write_bytes(b"fake plan")
    monkeypatch.setattr(terraform_exec, "terraform_available", lambda: True)
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProcess([b"applied\n"], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    ok = await terraform_exec.run_terraform("apply", terraform_dir, "aws", aws_creds, noop)

    assert ok is True
    assert calls[1] == ("terraform", "apply", "-input=false", "tfplan")


@pytest.mark.asyncio
async def test_apply_without_saved_plan_uses_auto_approve(monkeypatch, terraform_dir, aws_creds):
    monkeypatch.setattr(terraform_exec, "terraform_available", lambda: True)
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProcess([b"applied\n"], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    ok = await terraform_exec.run_terraform("apply", terraform_dir, "aws", aws_creds, noop)

    assert ok is True
    assert calls[1] == ("terraform", "apply", "-input=false", "-auto-approve")


@pytest.mark.asyncio
async def test_init_failure_stops_before_plan(monkeypatch, terraform_dir, aws_creds):
    monkeypatch.setattr(terraform_exec, "terraform_available", lambda: True)
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProcess([b"error\n"], returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    ok = await terraform_exec.run_terraform("plan", terraform_dir, "aws", aws_creds, noop)

    assert ok is False
    assert len(calls) == 1  # never got to "plan"


@pytest.mark.asyncio
async def test_missing_terraform_binary_short_circuits(monkeypatch, terraform_dir, aws_creds):
    monkeypatch.setattr(terraform_exec, "terraform_available", lambda: False)
    lines = []

    async def on_line(line):
        lines.append(line)

    ok = await terraform_exec.run_terraform("plan", terraform_dir, "aws", aws_creds, on_line)
    assert ok is False
    assert any("not installed" in line for line in lines)


@pytest.mark.asyncio
async def test_missing_main_tf_short_circuits(monkeypatch, tmp_path, aws_creds):
    monkeypatch.setattr(terraform_exec, "terraform_available", lambda: True)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    lines = []

    async def on_line(line):
        lines.append(line)

    ok = await terraform_exec.run_terraform("plan", empty_dir, "aws", aws_creds, on_line)
    assert ok is False
    assert any("no Terraform module" in line for line in lines)


@pytest.mark.asyncio
async def test_unknown_action_raises(terraform_dir, aws_creds):
    with pytest.raises(ValueError, match="unknown terraform action"):
        await terraform_exec.run_terraform("nuke", terraform_dir, "aws", aws_creds, noop)


@pytest.mark.asyncio
async def test_gcp_writes_and_cleans_up_credentials_file(monkeypatch, terraform_dir):
    monkeypatch.setattr(terraform_exec, "terraform_available", lambda: True)
    written_paths = []

    async def fake_exec(*args, **kwargs):
        env = kwargs["env"]
        cred_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path:
            written_paths.append(cred_path)
            assert Path(cred_path).read_text() == '{"k": "v"}'
        return FakeProcess([b"ok\n"], returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    creds = CloudCredentials(gcp_project="proj", gcp_credentials_json='{"k": "v"}')
    ok = await terraform_exec.run_terraform("plan", terraform_dir, "gcp", creds, noop)

    assert ok is True
    assert written_paths, "expected GOOGLE_APPLICATION_CREDENTIALS to be set"
    # cleaned up after the run
    assert not Path(written_paths[0]).exists()
