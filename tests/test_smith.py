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
    assert out["artifacts"]["reasoning"] == "ok"


@pytest.mark.asyncio
async def test_template_fallback_still_has_a_reasoning_string():
    with patch("deploymint.core.llm.complete", return_value="I'm sorry, I can't help."):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["generated_by"] == "template"
    assert out["artifacts"]["reasoning"]


@pytest.mark.asyncio
async def test_reasoning_detail_passes_through():
    good = (
        '{"dockerfile":"FROM python:3.11-slim\\nUSER 10001\\nCMD [\\"python\\"]",'
        '"dockerignore":"","k8s_deployment":"kind: Deployment\\nmetadata:\\n  name: t\\n'
        "spec:\\n  selector:\\n    matchLabels: {}\\n  template:\\n    metadata: {}\\n"
        "    spec:\\n      containers:\\n      - name: t\\n"
        '        image: x","k8s_service":"kind: Service\\nmetadata:\\n  name: t\\n'
        'spec:\\n  ports: []","reasoning":"short summary",'
        '"reasoning_detail":"Paragraph one.\\n\\nParagraph two."}'
    )
    with patch("deploymint.core.llm.complete", return_value=good):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["reasoning_detail"] == "Paragraph one.\n\nParagraph two."


@pytest.mark.asyncio
async def test_template_fallback_has_empty_reasoning_detail():
    with patch("deploymint.core.llm.complete", return_value="nope"):
        out = await ArtifactSmithAgent().run(dict(BASE))
    assert out["artifacts"]["reasoning_detail"] == ""


@pytest.mark.asyncio
async def test_llm_call_gets_an_explicit_larger_max_tokens():
    """Previously smith.py passed no max_tokens at all, inheriting
    llm.complete's default 4000 — shared across the Dockerfile, both YAML
    docs, and reasoning combined. See docs/29-richer-reasoning.md."""
    with patch("deploymint.core.llm.complete", return_value="nope") as mock_complete:
        await ArtifactSmithAgent().run(dict(BASE))
    _, kwargs = mock_complete.call_args
    assert kwargs["max_tokens"] > 4000


@pytest.mark.asyncio
async def test_prompt_includes_the_import_graph_and_services_now():
    """Regression test: TRIM_KEYS used to silently drop `graph`, `services`,
    and `dockerfile_exists` before the analysis ever reached the model — so
    asking for per-file rationale was asking for something the model
    couldn't see. See docs/29-richer-reasoning.md."""
    analysis = dict(ANALYSIS)
    analysis["graph"] = {"nodes": [{"id": "app/db.py"}, {"id": "app/routes.py"}],
                         "links": [{"source": "app/routes.py", "target": "app/db.py"}]}
    analysis["services"] = [{"name": "web", "path": ".", "port": None}]
    analysis["dockerfile_exists"] = True
    analysis["critical_files"] = ["app/db.py"]
    state = {**BASE, "analysis": analysis}

    with patch("deploymint.core.llm.complete", return_value="nope") as mock_complete:
        await ArtifactSmithAgent().run(state)
    # first call, not the last — "nope" fails validation, so the repair
    # attempt (a second, different call) follows it
    args, _ = mock_complete.call_args_list[0]
    user_prompt = args[1]
    assert "app/db.py" in user_prompt
    assert "import_graph_summary" in user_prompt
    assert '"services"' in user_prompt


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
