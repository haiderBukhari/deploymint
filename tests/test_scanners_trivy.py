import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deploymint.core import scanners

TRIVY_FS_OUTPUT = {
    "Results": [
        {
            "Target": "requirements.txt",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2024-12345",
                    "PkgName": "requests",
                    "InstalledVersion": "2.25.0",
                    "FixedVersion": "2.31.0",
                    "Severity": "CRITICAL",
                    "Title": "Improper certificate validation",
                }
            ],
        }
    ]
}

TRIVY_IMAGE_OUTPUT = {
    "Results": [
        {
            "Target": "deploymint/app:run_1 (debian 12.5)",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2023-9999",
                    "PkgName": "openssl",
                    "InstalledVersion": "3.0.11",
                    "FixedVersion": "",
                    "Severity": "high",
                    "Title": "some issue",
                }
            ],
            "Misconfigurations": [
                {"ID": "AVD-DS-0002", "Severity": "medium", "Title": "root user",
                 "Resolution": "Add a USER instruction"}
            ],
        }
    ]
}


def _mock_proc(stdout: bytes, returncode: int = 0):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_trivy_not_installed_returns_error_not_exception():
    with patch("deploymint.core.scanners.trivy_available", return_value=False):
        findings, err = await scanners.run_trivy_fs(Path("/tmp/whatever"))
    assert findings == []
    assert "not installed" in err


@pytest.mark.asyncio
async def test_trivy_fs_parses_vulnerabilities_and_lowercases_severity():
    proc = _mock_proc(json.dumps(TRIVY_FS_OUTPUT).encode())
    with patch("deploymint.core.scanners.trivy_available", return_value=True), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        findings, err = await scanners.run_trivy_fs(Path("/tmp/whatever"))
    assert err is None
    assert len(findings) == 1
    f = findings[0]
    assert f["id"] == "CVE-2024-12345"
    assert f["severity"] == "critical"
    assert f["source"] == "trivy"
    assert "requests" in f["message"]
    assert "2.31.0" in f["remediation"]


@pytest.mark.asyncio
async def test_trivy_image_parses_vulns_and_misconfigs_case_insensitively():
    proc = _mock_proc(json.dumps(TRIVY_IMAGE_OUTPUT).encode())
    with patch("deploymint.core.scanners.trivy_available", return_value=True), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        findings, err = await scanners.run_trivy_image("deploymint/app:run_1")
    assert err is None
    ids = {f["id"] for f in findings}
    assert "CVE-2023-9999" in ids
    assert "AVD-DS-0002" in ids
    by_id = {f["id"]: f for f in findings}
    assert by_id["CVE-2023-9999"]["severity"] == "high"
    assert by_id["AVD-DS-0002"]["severity"] == "medium"
    # No fixed version published — remediation must say so, not claim an upgrade.
    assert "No fixed version" in by_id["CVE-2023-9999"]["remediation"]


@pytest.mark.asyncio
async def test_trivy_nonzero_exit_is_reported_not_raised():
    proc = _mock_proc(b"", returncode=1)
    with patch("deploymint.core.scanners.trivy_available", return_value=True), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        findings, err = await scanners.run_trivy_fs(Path("/tmp/whatever"))
    assert findings == []
    assert "trivy exited 1" in err


@pytest.mark.asyncio
async def test_trivy_unparseable_output_is_reported_not_raised():
    proc = _mock_proc(b"not json")
    with patch("deploymint.core.scanners.trivy_available", return_value=True), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        findings, err = await scanners.run_trivy_fs(Path("/tmp/whatever"))
    assert findings == []
    assert "could not parse" in err
