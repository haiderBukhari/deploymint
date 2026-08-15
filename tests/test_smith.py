from unittest.mock import patch

import pytest

from deploymint.agents.smith import ArtifactSmithAgent

ANALYSIS = {
    "language": "python", "framework": "fastapi", "package_manager": "pip",
    "entrypoint": "main.py", "exposed_port": 8000, "python_version": "3.11",
    "dependencies": ["fastapi", "uvicorn"], "critical_files": [], "has_tests": True,
    "file_count": 6,
}
BASE = {
    "run_id": "run_test", "project_id": 1, "project_name": "t",
    "repo_path": "/workspace/t", "force": False, "errors": [], "current_node": "",
    "analysis": ANALYSIS,
}


@pytest.mark.asyncio
async def test_falls_back_to_template_when_llm_returns_garbage():
    with patch("deploymint.core.llm.complete", return_value="I'm sorry, I can't help."):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "template"
    assert "FROM python:3.11" in out["artifacts"]["dockerfile"]
    assert "errors" in out


@pytest.mark.asyncio
async def test_falls_back_when_api_is_unreachable():
    from deploymint.core.llm import LLMUnavailable

    with patch("deploymint.core.llm.complete", side_effect=LLMUnavailable("rate limited")):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "template"
    assert out["artifacts"]["dockerfile"]


@pytest.mark.asyncio
async def test_auth_error_falls_back_without_crashing_repair():
    """Regression test: an LLMError that is NOT LLMUnavailable (e.g. a real
    401 invalid-API-key response, wrapped by llm.complete as plain LLMError)
    must not attempt repair — self._last_raw was never set because the API
    call itself failed, so repair would crash with AttributeError. Found by
    actually running the shipped Docker Compose stack with a key that turned
    out to be invalid."""
    from deploymint.core.llm import LLMError

    with patch("deploymint.core.llm.complete", side_effect=LLMError("api error 401: invalid")):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "template"
    assert out["artifacts"]["dockerfile"]
    assert "repair failed" not in out["errors"][-1]


@pytest.mark.asyncio
async def test_strips_markdown_fences_and_injects_image():
    fenced = (
        '```json\n{"dockerfile":"FROM python:3.11-slim\\nUSER 10001\\n'
        'CMD [\\"python\\"]",'
        '"dockerignore":"","k8s_deployment":"kind: Deployment\\nmetadata:\\n  name: old\\n'
        "spec:\\n  selector:\\n    matchLabels: {}\\n  template:\\n    metadata: {}\\n"
        "    spec:\\n      containers:\\n      - name: old\\n"
        '        image: placeholder","k8s_service":"kind: Service\\nmetadata:\\n  name: old\\n'
        'spec:\\n  ports: []","reasoning":"ok"}\n```'
    )
    with patch("deploymint.core.llm.complete", return_value=fenced):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "llm"
    assert "```" not in out["artifacts"]["dockerfile"]
    # _inject_image must overwrite the model's placeholder tag with the real one
    assert "run_test" in out["artifacts"]["k8s_deployment"]


@pytest.mark.asyncio
async def test_repair_recovers_from_one_bad_attempt():
    bad = '{"dockerfile": "no from instruction here"}'
    good = (
        '{"dockerfile":"FROM python:3.11-slim\\nUSER 10001\\nCMD [\\"python\\"]",'
        '"dockerignore":"","k8s_deployment":"kind: Deployment\\nmetadata:\\n  name: t\\n'
        "spec:\\n  selector:\\n    matchLabels: {}\\n  template:\\n    metadata: {}\\n"
        "    spec:\\n      containers:\\n      - name: t\\n"
        '        image: x","k8s_service":"kind: Service\\nmetadata:\\n  name: t\\n'
        'spec:\\n  ports: []","reasoning":"ok"}'
    )
    with patch("deploymint.core.llm.complete", side_effect=[bad, good]):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "llm"
