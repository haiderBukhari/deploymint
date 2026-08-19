"""LLM-suggested fixes for a finding. See docs/28-ai-fix.md. The LLM is
always mocked — no test in this suite should need a real API key."""

from unittest.mock import patch

import pytest

from deploymint.core.fix_suggester import build_diff, suggest_fix

BEFORE = "FROM python:3.11-slim\nUSER root\nCMD [\"python\", \"main.py\"]\n"
AFTER = "FROM python:3.11-slim\nUSER 10001\nCMD [\"python\", \"main.py\"]\n"
FINDING = {"id": "CKV_DOCKER_3", "message": "Ensure the last USER is not root",
           "remediation": "Set a non-root USER"}


def test_build_diff_marks_added_and_removed_lines():
    diff = build_diff("Dockerfile", BEFORE, AFTER)
    assert "-USER root" in diff
    assert "+USER 10001" in diff
    assert "a/Dockerfile" in diff and "b/Dockerfile" in diff


def test_build_diff_is_empty_for_identical_content():
    assert build_diff("Dockerfile", BEFORE, BEFORE) == ""


@pytest.mark.asyncio
async def test_suggest_fix_returns_content_and_diff():
    with patch("deploymint.core.fix_suggester.llm.complete", return_value=AFTER):
        result = await suggest_fix(FINDING, BEFORE, "Dockerfile")
    assert result["suggested_content"] == AFTER.strip()
    assert result["changed"] is True
    assert "+USER 10001" in result["diff"]


@pytest.mark.asyncio
async def test_suggest_fix_reports_unchanged_when_model_returns_the_same_file():
    with patch("deploymint.core.fix_suggester.llm.complete", return_value=BEFORE):
        result = await suggest_fix(FINDING, BEFORE, "Dockerfile")
    assert result["changed"] is False


@pytest.mark.asyncio
async def test_suggest_fix_strips_markdown_fences():
    """Models wrap file content in ``` fences despite being told not to —
    shipping that verbatim into an artifact would corrupt the file."""
    fenced = f"```dockerfile\n{AFTER}```"
    with patch("deploymint.core.fix_suggester.llm.complete", return_value=fenced):
        result = await suggest_fix(FINDING, BEFORE, "Dockerfile")
    assert result["suggested_content"] == AFTER.strip()
    assert "```" not in result["suggested_content"]


@pytest.mark.asyncio
async def test_suggest_fix_strips_bare_fences_too():
    with patch("deploymint.core.fix_suggester.llm.complete", return_value=f"```\n{AFTER}```"):
        result = await suggest_fix(FINDING, BEFORE, "Dockerfile")
    assert result["suggested_content"] == AFTER.strip()


@pytest.mark.asyncio
async def test_suggest_fix_propagates_llm_failure():
    """Unlike the Warden's optional explanations, the suggestion IS the whole
    request — failing silently would hand back a bogus 'no change' result."""
    with patch("deploymint.core.fix_suggester.llm.complete",
               side_effect=RuntimeError("api down")):
        with pytest.raises(RuntimeError):
            await suggest_fix(FINDING, BEFORE, "Dockerfile")
