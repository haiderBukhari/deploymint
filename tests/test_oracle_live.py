"""The Oracle + Remediator's core demo moment, run for real: an app that
crashes immediately is built and deployed to the live cluster, the Oracle
detects CrashLoopBackOff, and the Remediator rolls it back (deleting the
deployment, since a first-ever deploy has no previous revision to undo to).
See docs/10-phase-6-finops-ui.md §6.1-6.2 and §6.5.

Skipped automatically if no real docker+kubernetes is reachable — same gate
as tests/test_execution.py."""

import shutil
import subprocess
import time

import pytest


def _cluster_reachable() -> bool:
    if not (shutil.which("docker") and shutil.which("kubectl")):
        return False
    try:
        r = subprocess.run(["kubectl", "cluster-info", "--request-timeout=5s"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _cluster_reachable(), reason="no reachable docker+kubernetes for a real rollback test"
)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_crashing_app_is_detected_and_rolled_back_for_real(client, workspace, monkeypatch):
    import shutil as sh

    from deploymint.config import get_settings

    # The workspace fixture shrinks the Oracle's watch window for the fast
    # suite (2 samples x 1s) — too short to reliably observe a real pod crash
    # and restart. Restore a real window for this one live test.
    monkeypatch.setenv("DEPLOYMINT_ORACLE_SAMPLES", "12")
    monkeypatch.setenv("DEPLOYMINT_ORACLE_INTERVAL", "5")
    get_settings.cache_clear()

    dst = workspace / "crashy_app"
    sh.copytree("tests/fixtures/crashy_app", dst)

    r = client.post("/api/projects", json={"name": "crashy-app", "repo_path": str(dst)})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    run_id = client.post(f"/api/projects/{pid}/runs", json={}).json()["run_id"]

    deadline = time.monotonic() + 180
    run = None
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"success", "failed", "blocked", "cancelled"}:
            break
        time.sleep(2)

    dep = (run or {}).get("deployment") or {}
    try:
        assert run["status"] == "failed", run
        assert dep["status"] == "rolled_back"
        assert dep.get("anomaly_explanation")
        assert any("oracle:" in e for e in run["errors"])

        out = subprocess.run(
            ["kubectl", "get", "deployment", "crashy-app"],
            capture_output=True, text=True,
        )
        assert out.returncode != 0, "deployment should have been removed by the Remediator"
    finally:
        subprocess.run(["kubectl", "delete", "deployment", "crashy-app", "--ignore-not-found"],
                       capture_output=True)
        subprocess.run(["kubectl", "delete", "service", "crashy-app-svc", "--ignore-not-found"],
                       capture_output=True)
        if dep.get("image_tag"):
            subprocess.run(["docker", "rmi", "-f", dep["image_tag"]], capture_output=True)
