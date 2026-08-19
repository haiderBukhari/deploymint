"""The suggest-fix / apply-fix endpoints. See docs/28-ai-fix.md. The LLM is
always mocked; the Warden re-scan runs for real (it's just Checkov/OPA
subprocesses, same as any other test that exercises the gate)."""

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
    raise TimeoutError(f"run {run_id} did not finish in {timeout}s")


@pytest.fixture
def finished_run(client, registered_project):
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    run = _wait_for_terminal(client, run_id)
    assert run["status"] == "success", run["errors"]
    return run


def test_suggest_fix_returns_a_diff(client, finished_run):
    patched = finished_run["artifacts"]["dockerfile"].replace(
        "python:3.11-slim", "python:3.11.9-slim")
    with patch("deploymint.core.fix_suggester.llm.complete", return_value=patched):
        r = client.post(
            f"/api/runs/{finished_run['id']}/findings/suggest-fix",
            json={"file": "Dockerfile", "finding_id": "RT_NO_DIGEST"})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] is True
    assert "python:3.11.9-slim" in body["suggested_content"]
    assert "+FROM python:3.11.9-slim" in body["diff"]


def test_suggest_fix_404s_for_an_unknown_run(client):
    r = client.post("/api/runs/nope/findings/suggest-fix",
                    json={"file": "Dockerfile", "finding_id": "X"})
    assert r.status_code == 404


def test_suggest_fix_400s_for_a_non_artifact_file(client, finished_run):
    r = client.post(f"/api/runs/{finished_run['id']}/findings/suggest-fix",
                    json={"file": "../../etc/passwd", "finding_id": "X"})
    assert r.status_code == 400


def test_suggest_fix_404s_for_a_finding_not_on_this_run(client, finished_run):
    r = client.post(f"/api/runs/{finished_run['id']}/findings/suggest-fix",
                    json={"file": "Dockerfile", "finding_id": "CKV_DOES_NOT_EXIST"})
    assert r.status_code == 404


def test_suggest_fix_502s_when_the_llm_is_unavailable(client, finished_run):
    with patch("deploymint.core.fix_suggester.llm.complete",
               side_effect=RuntimeError("api down")):
        r = client.post(f"/api/runs/{finished_run['id']}/findings/suggest-fix",
                        json={"file": "Dockerfile", "finding_id": "RT_NO_DIGEST"})
    assert r.status_code == 502


def test_apply_fix_creates_a_new_run_and_leaves_the_original_untouched(client, finished_run):
    """The original run must never be mutated — this product's audit-trail
    guarantee depends on a verified run's artifacts staying as verified."""
    original_id = finished_run["id"]
    original_dockerfile = finished_run["artifacts"]["dockerfile"]
    patched = original_dockerfile.replace("python:3.11-slim", "python:3.11.9-slim")

    r = client.post(f"/api/runs/{original_id}/findings/apply-fix",
                    json={"file": "Dockerfile", "patched_content": patched})
    assert r.status_code == 201
    body = r.json()
    assert body["run_id"] != original_id
    assert body["status"] in {"success", "blocked"}
    assert "findings" in body

    new_run = client.get(f"/api/runs/{body['run_id']}").json()
    assert new_run["artifacts"]["dockerfile"] == patched
    assert new_run["security"] is not None  # the gate really re-ran

    still_original = client.get(f"/api/runs/{original_id}").json()
    assert still_original["artifacts"]["dockerfile"] == original_dockerfile
    assert still_original["status"] == "success"


def test_apply_fix_404s_for_an_unknown_run(client):
    r = client.post("/api/runs/nope/findings/apply-fix",
                    json={"file": "Dockerfile", "patched_content": "FROM scratch"})
    assert r.status_code == 404
