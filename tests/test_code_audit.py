from unittest.mock import patch

import pytest

from deploymint.agents.code_audit import CodeAuditAgent


class _On:
    enable_code_audit = True


class _Off:
    enable_code_audit = False


def _state(repo_path, **overrides):
    base = {"run_id": "run_ca", "repo_path": str(repo_path),
           "analysis": {"dependencies": []}, "errors": []}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_disabled_setting_is_a_noop(tmp_path):
    with patch("deploymint.agents.code_audit.get_settings", return_value=_Off()):
        out = await CodeAuditAgent().run(_state(tmp_path))
    assert out == {}


@pytest.mark.asyncio
async def test_empty_repo_produces_no_findings_and_no_llm_call(tmp_path):
    with patch("deploymint.agents.code_audit.get_settings", return_value=_On()), \
         patch("deploymint.core.llm.complete_json") as mock_llm:
        out = await CodeAuditAgent().run(_state(tmp_path))
    mock_llm.assert_not_called()
    assert out["security"]["code_audit_ran"] is True
    assert out["security"]["findings"] == []


@pytest.mark.asyncio
async def test_finds_a_planted_secret_with_real_file_and_line(tmp_path):
    (tmp_path / "config.py").write_text(
        "import os\nAPI_KEY = 'sk-live-abcdef1234567890'\n")
    hallucinated = (
        '{"findings": [{"id":"CA_001","severity":"critical","file":"config.py",'
        '"line":2,"message":"Hardcoded API key","remediation":"Use an env var."}]}'
    )
    with patch("deploymint.agents.code_audit.get_settings", return_value=_On()), \
         patch("deploymint.core.llm.complete", return_value=hallucinated):
        out = await CodeAuditAgent().run(_state(tmp_path))
    sec = out["security"]
    assert sec["code_audit_ran"] is True
    finding = sec["findings"][0]
    assert finding["source"] == "code_audit"
    assert finding["file"] == "config.py"
    assert finding["line"] == 2
    # A true LLM-reported "critical" clamps to "high" — same trust boundary
    # as redteam.py, an LLM alone never blocks a deploy.
    assert finding["severity"] == "high"
    assert sec["passed"] is True


@pytest.mark.asyncio
async def test_llm_failure_produces_an_info_finding_not_a_crash(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    with patch("deploymint.agents.code_audit.get_settings", return_value=_On()), \
         patch("deploymint.core.llm.complete", side_effect=RuntimeError("no api key")):
        out = await CodeAuditAgent().run(_state(tmp_path))
    sec = out["security"]
    assert sec["code_audit_ran"] is True
    assert sec["findings"][0]["id"] == "CA_LLM_UNAVAILABLE"
    assert sec["findings"][0]["severity"] == "info"
    assert sec["passed"] is True


@pytest.mark.asyncio
async def test_counts_recomputed_after_appending_findings(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    hallucinated = (
        '{"findings": [{"id":"CA_002","severity":"medium","file":"app.py",'
        '"line":1,"message":"x","remediation":"y"}]}'
    )
    state = _state(tmp_path)
    state["security"] = {"passed": True, "findings": [], "counts": {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}}
    with patch("deploymint.agents.code_audit.get_settings", return_value=_On()), \
         patch("deploymint.core.llm.complete", return_value=hallucinated):
        out = await CodeAuditAgent().run(state)
    sec = out["security"]
    assert sec["counts"]["medium"] == 1
    assert sum(sec["counts"].values()) == len(sec["findings"])


@pytest.mark.asyncio
async def test_critical_files_are_prioritized_over_other_files(tmp_path):
    """A budget-exhausted repo should still send the critical file, since
    _order_by_criticality puts it first."""
    from deploymint.agents.code_audit import _order_by_criticality

    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("x = 1\n")
    b.write_text("y = 2\n")
    ordered = _order_by_criticality([a, b], tmp_path, ["b.py"])
    assert ordered[0] == b


@pytest.mark.asyncio
async def test_env_files_are_included_regardless_of_extension(tmp_path):
    from deploymint.agents.code_audit import _relevant_files

    env = tmp_path / ".env"
    readme = tmp_path / "README.md"
    env.write_text("SECRET=123\n")
    readme.write_text("hello\n")
    relevant = _relevant_files(tmp_path, [env, readme])
    assert env in relevant
    assert readme not in relevant
