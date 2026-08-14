"""Real end-to-end execution: builds the generated Dockerfile on the actual
host Docker daemon and deploys it to whatever cluster kubectl currently
reaches (a local `kind` cluster in dev). See docs/08-phase-4-execution.md §4.9.

Skipped automatically if docker/kubectl aren't on PATH or no cluster is
reachable — this is the one test in the suite that touches real
infrastructure rather than mocks."""

import shutil
import subprocess

import pytest

from deploymint.core.audit import verify_chain


def _cluster_reachable() -> bool:
    if not (shutil.which("docker") and shutil.which("kubectl")):
        return False
    try:
        r = subprocess.run(
            ["kubectl", "cluster-info", "--request-timeout=5s"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _cluster_reachable(), reason="no reachable docker+kubernetes for a real build/deploy"
)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_full_pipeline_builds_and_deploys_to_the_real_cluster(client, registered_project):
    pid = registered_project["id"]
    run_id = client.post(f"/api/projects/{pid}/runs", json={}).json()["run_id"]

    import time

    deadline = time.monotonic() + 180
    run = None
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"success", "failed", "blocked", "cancelled"}:
            break
        time.sleep(1)

    try:
        assert run["status"] == "success", run.get("errors")
        dep = run["deployment"]
        assert dep["status"] == "running"
        assert dep["mode"] in ("kubernetes", "docker")

        if dep["mode"] == "kubernetes":
            assert dep["pod_name"]
            out = subprocess.run(
                ["kubectl", "get", "pod", dep["pod_name"], "-o",
                 "jsonpath={.status.phase}"],
                capture_output=True, text=True,
            )
            assert out.stdout.strip() == "Running"
        else:
            assert dep["container_id"]
            out = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", dep["container_id"]],
                capture_output=True, text=True,
            )
            assert out.stdout.strip() == "true"

        verdict = verify_chain(run_id)
        assert verdict["valid"] is True
        assert verdict["entries"] >= 1

        from pathlib import Path

        session_log = Path(dep["session_file"])
        assert session_log.exists()
        assert "docker build" in session_log.read_text()

    finally:
        name = registered_project["name"]
        subprocess.run(["kubectl", "delete", "deployment", name, "--ignore-not-found"],
                       capture_output=True)
        subprocess.run(["kubectl", "delete", "service", f"{name}-svc", "--ignore-not-found"],
                       capture_output=True)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
