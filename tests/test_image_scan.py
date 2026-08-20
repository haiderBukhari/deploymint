from unittest.mock import AsyncMock, patch

import pytest

from deploymint.agents.image_scan import ImageScanAgent


class _TrivyOn:
    enable_trivy = True


class _TrivyOff:
    enable_trivy = False


FINDINGS = [{"id": "CVE-1", "severity": "critical", "source": "trivy",
            "file": "app:t", "message": "bad", "remediation": "fix it"}]


@pytest.mark.asyncio
async def test_scans_the_built_image_and_merges_findings():
    state = {"deployment": {"image_tag": "deploymint/app:run_1"},
             "security": {"passed": True, "findings": []}}
    with patch("deploymint.agents.image_scan.get_settings", return_value=_TrivyOn()), \
         patch("deploymint.core.scanners.run_trivy_image",
               new=AsyncMock(return_value=(FINDINGS, None))):
        out = await ImageScanAgent().run(state)
    sec = out["security"]
    assert sec["trivy_image_ran"] is True
    assert sec["findings"][0]["id"] == "CVE-1"


@pytest.mark.asyncio
async def test_counts_recomputed_after_appending_image_findings():
    state = {"deployment": {"image_tag": "deploymint/app:run_1"},
             "security": {"passed": True, "findings": [],
                          "counts": {"critical": 0, "high": 0, "medium": 0,
                                     "low": 0, "info": 0}}}
    with patch("deploymint.agents.image_scan.get_settings", return_value=_TrivyOn()), \
         patch("deploymint.core.scanners.run_trivy_image",
               new=AsyncMock(return_value=(FINDINGS, None))):
        out = await ImageScanAgent().run(state)
    sec = out["security"]
    assert sec["counts"]["critical"] == 1
    assert sum(sec["counts"].values()) == len(sec["findings"])


@pytest.mark.asyncio
async def test_never_blocks_the_already_deployed_run():
    """Findings surface for the next fix cycle — the deploy already
    happened, so a post-hoc image CVE must not flip `passed`."""
    state = {"deployment": {"image_tag": "deploymint/app:run_1"},
             "security": {"passed": True, "findings": []}}
    with patch("deploymint.agents.image_scan.get_settings", return_value=_TrivyOn()), \
         patch("deploymint.core.scanners.run_trivy_image",
               new=AsyncMock(return_value=(FINDINGS, None))):
        out = await ImageScanAgent().run(state)
    assert out["security"]["passed"] is True


@pytest.mark.asyncio
async def test_disabled_setting_is_a_noop():
    state = {"deployment": {"image_tag": "deploymint/app:run_1"},
             "security": {"passed": True, "findings": []}}
    with patch("deploymint.agents.image_scan.get_settings", return_value=_TrivyOff()):
        out = await ImageScanAgent().run(state)
    assert out == {}


@pytest.mark.asyncio
async def test_missing_image_tag_is_a_noop():
    state = {"deployment": {}, "security": {"passed": True, "findings": []}}
    with patch("deploymint.agents.image_scan.get_settings", return_value=_TrivyOn()):
        out = await ImageScanAgent().run(state)
    assert out == {}


@pytest.mark.asyncio
async def test_scanner_error_sets_ran_false_without_raising():
    state = {"deployment": {"image_tag": "deploymint/app:run_1"},
             "security": {"passed": True, "findings": []}}
    with patch("deploymint.agents.image_scan.get_settings", return_value=_TrivyOn()), \
         patch("deploymint.core.scanners.run_trivy_image",
               new=AsyncMock(return_value=([], "trivy not installed"))):
        out = await ImageScanAgent().run(state)
    assert out["security"]["trivy_image_ran"] is False
