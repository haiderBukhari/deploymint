"""See docs/21-cloud-deploy.md. Mocks core.terraform_exec.run_terraform so
this suite never shells out to a real terraform binary or cloud account."""

import time
from unittest.mock import patch

import pytest


def _wait_for_terminal(client, run_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = client.get(f"/api/runs/{run_id}").json()
        if got["status"] in {"success", "failed", "blocked", "cancelled"}:
            return got
        time.sleep(0.1)
    raise TimeoutError(f"run {run_id} did not reach a terminal status in {timeout}s")


async def _fake_ok(action, directory, cloud, creds, on_line):
    await on_line(f"[deploymint] $ terraform {action}")
    await on_line(f"[deploymint] terraform {action} completed successfully")
    return True


def _wait_for_job(client, run_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = client.get(f"/api/runs/{run_id}/cloud/status").json()
        if got["status"] in {"success", "failed"}:
            return got
        time.sleep(0.05)
    raise TimeoutError(f"cloud deploy for {run_id} did not finish in {timeout}s")


@pytest.fixture
async def successful_run(client, registered_project):
    pid = registered_project["id"]
    r = client.post(f"/api/projects/{pid}/runs", json={"skip_deploy": True})
    run_id = r.json()["run_id"]
    run = _wait_for_terminal(client, run_id)
    assert run["status"] == "success", run["errors"]
    return run_id


AWS_CREDS = {
    "aws_access_key_id": "AKIA_TEST",
    "aws_secret_access_key": "secret",
    "aws_region": "us-east-1",
}


@pytest.mark.asyncio
async def test_plan_starts_and_streams_to_status(client, successful_run):
    with patch("deploymint.api.cloud_deploy.run_terraform", _fake_ok):
        r = client.post(f"/api/runs/{successful_run}/cloud/plan", json=AWS_CREDS)
        assert r.status_code == 202

        result = _wait_for_job(client, successful_run)
        assert result["status"] == "success"
        assert result["action"] == "plan"
        assert "completed successfully" in result["output"]


@pytest.mark.asyncio
async def test_unknown_run_is_404(client):
    r = client.post("/api/runs/does-not-exist/cloud/plan", json=AWS_CREDS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unknown_action_is_404(client, successful_run):
    r = client.post(f"/api/runs/{successful_run}/cloud/nuke", json=AWS_CREDS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_missing_credentials_is_400(client, successful_run):
    r = client.post(f"/api/runs/{successful_run}/cloud/plan", json={})
    assert r.status_code == 400
    assert "aws_access_key_id" in r.json()["detail"]


@pytest.mark.asyncio
async def test_non_successful_run_is_rejected(client, registered_project):
    pid = registered_project["id"]
    r = client.post(f"/api/projects/{pid}/runs", json={"skip_deploy": True})
    run_id = r.json()["run_id"]
    # Don't wait for terminal — still pending/running.
    r2 = client.post(f"/api/runs/{run_id}/cloud/plan", json=AWS_CREDS)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_deploy_is_rejected(client, successful_run):
    async def _slow_ok(action, directory, cloud, creds, on_line):
        import asyncio
        await asyncio.sleep(0.3)
        return True

    with patch("deploymint.api.cloud_deploy.run_terraform", _slow_ok):
        r1 = client.post(f"/api/runs/{successful_run}/cloud/plan", json=AWS_CREDS)
        assert r1.status_code == 202
        r2 = client.post(f"/api/runs/{successful_run}/cloud/plan", json=AWS_CREDS)
        assert r2.status_code == 409


@pytest.mark.asyncio
async def test_blank_form_falls_back_to_stored_credential(client, successful_run):
    """The whole point of the Monitoring page's global credentials — a
    blank Cloud Deploy form must still work if a credential is saved.
    See docs/36-monitoring.md."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    with patch.dict("os.environ", {"DEPLOYMINT_SECRET_KEY": key}):
        r = client.post("/api/settings/credentials/aws", json=AWS_CREDS)
        assert r.status_code == 201

        with patch("deploymint.api.cloud_deploy.run_terraform", _fake_ok):
            r = client.post(f"/api/runs/{successful_run}/cloud/plan", json={})
            assert r.status_code == 202, r.json()
            _wait_for_job(client, successful_run)


@pytest.mark.asyncio
async def test_per_request_credentials_override_the_stored_one(client, successful_run):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    with patch.dict("os.environ", {"DEPLOYMINT_SECRET_KEY": key}):
        client.post("/api/settings/credentials/aws", json=AWS_CREDS)

        captured = {}

        async def _capture(action, directory, cloud, creds, on_line):
            captured["access_key"] = creds.aws_access_key_id
            return True

        with patch("deploymint.api.cloud_deploy.run_terraform", _capture):
            override = {**AWS_CREDS, "aws_access_key_id": "AKIA_OVERRIDE"}
            client.post(f"/api/runs/{successful_run}/cloud/plan", json=override)
            _wait_for_job(client, successful_run)
    assert captured["access_key"] == "AKIA_OVERRIDE"


@pytest.mark.asyncio
async def test_apply_persists_status_onto_the_run_row(client, successful_run):
    with patch("deploymint.api.cloud_deploy.run_terraform", _fake_ok):
        client.post(f"/api/runs/{successful_run}/cloud/apply", json=AWS_CREDS)
        _wait_for_job(client, successful_run)

    run = client.get(f"/api/runs/{successful_run}").json()
    assert run["cloud_deploy_status"] == "success"
    assert run["cloud_deploy_action"] == "apply"
    assert run["cloud_deployed_at"] is not None
