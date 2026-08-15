"""Real end-to-end execution via the `docker run` fallback path specifically
— forced by pointing at an unreachable kube context, since a real kind
cluster is always up in this dev environment and test_execution.py would
otherwise only ever exercise the Kubernetes path.

This path had no coverage at all before this test: every other test either
mocks execution or runs where a real cluster is reachable. It was only
caught by actually bringing up the shipped Docker Compose stack (where the
mounted kubeconfig can't reach the host's kind cluster) that `docker run`
was hardcoding the same host port as the container port — colliding with
DeployMint's own dashboard port whenever a deployed app's exposed port
matches it (commonly 8000). See docs/16-decisions-log.md's Phase 7
correction entry and docker_run.py's module docstring."""

import shutil
import subprocess
import time

import pytest


def _docker_reachable() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_reachable(), reason="no reachable docker for a real docker-run fallback test"
)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_docker_run_fallback_picks_a_free_host_port(
    client, registered_project, monkeypatch
):
    from deploymint.config import get_settings

    monkeypatch.setenv("DEPLOYMINT_KUBE_CONTEXT", "definitely-not-a-real-context")
    get_settings.cache_clear()

    pid = registered_project["id"]
    run_id = client.post(f"/api/projects/{pid}/runs", json={}).json()["run_id"]

    deadline = time.monotonic() + 120
    run = None
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"success", "failed", "blocked", "cancelled"}:
            break
        time.sleep(1)

    get_settings.cache_clear()
    name = registered_project["name"]
    try:
        assert run["status"] == "success", run.get("errors")
        dep = run["deployment"]
        assert dep["mode"] == "docker"
        assert dep["container_id"]

        # The exposed port (8000, matching this repo's fixture) must NOT be
        # what local_url reports — that's the exact collision this test guards.
        assert dep["local_url"] != "http://localhost:8000"
        assigned_port = int(dep["local_url"].rsplit(":", 1)[-1])
        assert assigned_port != 8000

        r = subprocess.run(["curl", "-sf", dep["local_url"] + "/health"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
