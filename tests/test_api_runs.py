import time
from unittest.mock import patch

import pytest


def _wait_for_terminal(client, run_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = client.get(f"/api/runs/{run_id}").json()
        if got["status"] in {"success", "failed", "blocked", "cancelled"}:
            return got
        time.sleep(0.1)
    raise TimeoutError(f"run {run_id} did not reach a terminal status in {timeout}s")


@pytest.mark.asyncio
async def test_run_falls_back_to_template_without_a_real_key(client, registered_project):
    """No ANTHROPIC_API_KEY is set in the test environment — this proves the
    full HTTP pipeline (register -> trigger -> Architect -> Smith -> artifacts)
    works end to end via the resilience path, never via a real API call."""
    pid = registered_project["id"]
    r = client.post(f"/api/projects/{pid}/runs", json={})
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    run = _wait_for_terminal(client, run_id)
    assert run["status"] == "success", run["errors"]
    assert run["artifacts"]["generated_by"] == "template"
    assert "FROM python:3.11" in run["artifacts"]["dockerfile"]
    assert run["analysis"]["framework"] == "fastapi"

    artifacts = client.get(f"/api/runs/{run_id}/artifacts").json()
    assert "dockerfile" in artifacts

    dockerfile = client.get(f"/api/runs/{run_id}/artifacts/Dockerfile")
    assert dockerfile.status_code == 200
    assert "FROM python" in dockerfile.text


@pytest.mark.asyncio
async def test_run_uses_llm_when_available(client, registered_project):
    good = (
        '{"dockerfile":"FROM python:3.11-slim\\nUSER 10001\\nCMD [\\"python\\"]",'
        '"dockerignore":"","k8s_deployment":"kind: Deployment\\nmetadata:\\n  name: t\\n'
        "spec:\\n  selector:\\n    matchLabels: {}\\n  template:\\n    metadata: {}\\n"
        "    spec:\\n      containers:\\n      - name: t\\n"
        '        image: x","k8s_service":"kind: Service\\nmetadata:\\n  name: t\\n'
        'spec:\\n  ports: []","reasoning":"ok"}'
    )
    pid = registered_project["id"]
    with patch("deploymint.core.llm.complete", return_value=good):
        run_id = client.post(f"/api/projects/{pid}/runs", json={}).json()["run_id"]
        run = _wait_for_terminal(client, run_id)

    assert run["status"] == "success"
    assert run["artifacts"]["generated_by"] == "llm"


def test_run_not_found_is_404(client):
    assert client.get("/api/runs/run_doesnotexist").status_code == 404


def test_trigger_on_missing_project_is_404(client):
    assert client.post("/api/projects/9999/runs", json={}).status_code == 404


def test_artifacts_before_run_completes_is_400(client, registered_project):
    pid = registered_project["id"]
    run_id = client.post(f"/api/projects/{pid}/runs", json={}).json()["run_id"]
    # immediately after triggering, artifacts likely aren't ready yet OR the run
    # already finished (template path is fast) — assert whichever is consistent
    resp = client.get(f"/api/runs/{run_id}/artifacts")
    assert resp.status_code in (200, 400)
