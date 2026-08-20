"""POST /api/runs/{run_id}/approve — the architecture approval gate's resume
endpoint. See docs/33-deploy-lock-and-findings.md."""

from unittest.mock import patch

APPROVE_BODY = {
    "replicas": 3, "port": 9090, "cpu_request": "150m", "cpu_limit": "600m",
    "memory_request": "200Mi", "memory_limit": "700Mi", "cloud_provider": "aws",
    "provision_cluster": False, "deploy_mode": "docker",
}


def test_approve_404s_for_unknown_run(client):
    r = client.post("/api/runs/run_doesnotexist/approve", json=APPROVE_BODY)
    assert r.status_code == 404


def test_approve_409s_when_run_is_not_awaiting_approval(client, registered_project):
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    import time

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if client.get(f"/api/runs/{run_id}").json()["status"] != "running":
            break
        time.sleep(0.1)

    r = client.post(f"/api/runs/{run_id}/approve", json=APPROVE_BODY)
    assert r.status_code == 409


def test_approve_happy_path_stores_plan_and_resumes(client, registered_project):
    from deploymint.db.database import get_session_factory
    from deploymint.db.models import Run

    run_id = "run_awaiting_test"
    Session = get_session_factory()
    with Session() as db:
        db.add(Run(id=run_id, project_id=registered_project["id"], status="awaiting_approval",
                   trigger="web", force=False, errors=[],
                   analysis={"language": "python", "framework": "fastapi",
                            "entrypoint": "main.py", "exposed_port": 8000}))
        db.commit()

    with patch("deploymint.api.approvals.resume_from_approval") as mock_resume:
        r = client.post(f"/api/runs/{run_id}/approve", json=APPROVE_BODY)
    assert r.status_code == 202
    assert r.json() == {"run_id": run_id, "status": "running"}
    mock_resume.assert_called_once()
    called_run, called_project, called_plan = mock_resume.call_args[0]
    assert called_run.id == run_id
    assert called_plan["replicas"] == 3
    assert called_plan["port"] == 9090


def test_approve_400s_on_a_malformed_body(client, registered_project):
    from deploymint.db.database import get_session_factory
    from deploymint.db.models import Run

    run_id = "run_bad_body_test"
    Session = get_session_factory()
    with Session() as db:
        db.add(Run(id=run_id, project_id=registered_project["id"],
                   status="awaiting_approval", trigger="web", force=False, errors=[]))
        db.commit()

    r = client.post(f"/api/runs/{run_id}/approve",
                    json={"replicas": "not-a-number"})
    assert r.status_code == 422
