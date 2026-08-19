"""Checkov + OPA subprocess runners. See docs/07-phase-3-security.md §3.3-3.4.

Two decisions worth understanding:
- Checkov exit code 1 is success-with-findings, not a crash. Only >1 is a real failure.
- A scanner failure returns (findings, error_string), never raises. The Warden
  decides what a missing scanner means; the runner never sees an exception.
"""

import asyncio
import json
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

import yaml

CHECKOV_SEVERITY = {
    "CKV_DOCKER_3": "high", "CKV_DOCKER_2": "medium", "CKV_DOCKER_7": "high",
    "CKV_DOCKER_4": "medium", "CKV_DOCKER_5": "low",
    "CKV_K8S_8": "medium", "CKV_K8S_9": "medium",
    "CKV_K8S_10": "high", "CKV_K8S_11": "high",
    "CKV_K8S_12": "high", "CKV_K8S_13": "high",
    "CKV_K8S_20": "critical", "CKV_K8S_23": "critical",
    "CKV_K8S_28": "high", "CKV_K8S_37": "high", "CKV_K8S_38": "medium",
    "CKV_K8S_40": "medium", "CKV_K8S_43": "low",
}
DEFAULT_SEVERITY = "medium"


def checkov_available() -> bool:
    return shutil.which("checkov") is not None


async def run_checkov(directory: Path) -> tuple[list[dict], str | None]:
    if not checkov_available():
        return [], "checkov not installed (pip install checkov)"

    proc = await asyncio.create_subprocess_exec(
        "checkov", "--directory", str(directory),
        "--framework", "dockerfile", "--framework", "kubernetes",
        "--framework", "terraform", "--framework", "github_actions",
        "--output", "json", "--quiet", "--compact",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    except TimeoutError:
        proc.kill()
        return [], "checkov timed out after 120s"

    if proc.returncode not in (0, 1):
        return [], f"checkov exited {proc.returncode}: {err.decode()[:300]}"

    try:
        data = json.loads(out.decode() or "[]")
    except json.JSONDecodeError as e:
        return [], f"could not parse checkov output: {e}"

    blocks = data if isinstance(data, list) else [data]
    findings = []
    for block in blocks:
        for fc in (block.get("results") or {}).get("failed_checks", []):
            cid = fc.get("check_id", "CKV_UNKNOWN")
            lines = fc.get("file_line_range") or [0]
            findings.append({
                "id": cid,
                "severity": CHECKOV_SEVERITY.get(cid, DEFAULT_SEVERITY),
                "source": "checkov",
                "file": Path(fc.get("file_path", "")).name,
                "line": lines[0],
                "message": fc.get("check_name", ""),
                "remediation": fc.get("guideline") or "See the Checkov docs for this check.",
            })
    return findings, None


def policies_dir() -> Path:
    return Path(str(files("deploymint") / "policies"))


def dockerfile_to_opa_input(content: str) -> dict:
    return {
        "kind": "dockerfile",
        "lines": [line.rstrip() for line in content.splitlines()
                  if line.strip() and not line.strip().startswith("#")],
        "content": content,
    }


def yaml_to_opa_input(content: str) -> dict | None:
    try:
        doc = yaml.safe_load(content)
        return doc if isinstance(doc, dict) and "kind" in doc else None
    except yaml.YAMLError:
        return None


async def _opa_eval(input_doc: dict) -> tuple[list[dict], str | None]:
    if not shutil.which("opa"):
        return [], "opa not installed (brew install opa)"

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(input_doc, f)
        input_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "opa", "eval", "--format", "json",
            "--input", input_path, "--data", str(policies_dir()),
            "data.deploymint",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return [], f"opa failed: {err.decode()[:300]}"

        value = json.loads(out.decode())["result"][0]["expressions"][0]["value"]
        findings = []
        for pkg_name, pkg in (value or {}).items():
            for msg in (pkg or {}).get("deny", []):
                if isinstance(msg, dict):
                    findings.append({**msg, "source": "opa",
                                     "file": input_doc.get("kind", "unknown")})
                else:
                    findings.append({
                        "id": f"DM_{pkg_name.upper()}", "severity": "medium",
                        "source": "opa", "file": input_doc.get("kind", "unknown"),
                        "message": str(msg), "remediation": "",
                    })
        return findings, None
    finally:
        Path(input_path).unlink(missing_ok=True)


async def run_opa(artifacts: dict) -> tuple[list[dict], str | None]:
    all_findings, errors = [], []
    inputs = [dockerfile_to_opa_input(artifacts.get("dockerfile", ""))]
    for key in ("k8s_deployment", "k8s_service"):
        doc = yaml_to_opa_input(artifacts.get(key, ""))
        if doc:
            inputs.append(doc)

    for doc in inputs:
        f, e = await _opa_eval(doc)
        all_findings.extend(f)
        if e:
            errors.append(e)
    return all_findings, ("; ".join(errors) or None)


# Trivy fills the one gap Checkov/OPA don't cover: real, versioned CVEs in
# actual dependencies and OS packages — Checkov/OPA only catch
# *misconfigurations* (a privileged container, a missing digest pin), never
# "this exact version of requests has a known RCE". Unlike CHECKOV_SEVERITY
# above (a hand-maintained map, because free-tier Checkov emits none), Trivy
# reports its own `Severity` per finding — this just needs lowercasing.
TRIVY_SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium",
                  "LOW": "low", "UNKNOWN": "info"}


def trivy_available() -> bool:
    return shutil.which("trivy") is not None


def _trivy_findings_from_results(results: list[dict], *, source_tag: str) -> list[dict]:
    findings = []
    for result in results or []:
        target = result.get("Target", "")
        for vuln in result.get("Vulnerabilities") or []:
            cve = vuln.get("VulnerabilityID", "CVE_UNKNOWN")
            pkg = vuln.get("PkgName", "")
            installed = vuln.get("InstalledVersion", "")
            fixed = vuln.get("FixedVersion", "")
            remediation = (
                f"Upgrade {pkg} to {fixed}." if fixed
                else f"No fixed version published yet for {pkg} {installed}."
            )
            findings.append({
                "id": cve,
                "severity": TRIVY_SEVERITY.get(vuln.get("Severity", "").upper(), "info"),
                "source": "trivy",
                "file": Path(target).name or target,
                "message": f"{pkg} {installed}: {vuln.get('Title') or cve}",
                "remediation": remediation,
            })
        for mc in result.get("Misconfigurations") or []:
            findings.append({
                "id": mc.get("ID", "TRIVY_MISCONFIG"),
                "severity": TRIVY_SEVERITY.get((mc.get("Severity") or "").upper(), "info"),
                "source": "trivy",
                "file": Path(target).name or target,
                "message": mc.get("Title") or mc.get("Message", ""),
                "remediation": mc.get("Resolution") or "See the Trivy docs for this check.",
            })
    return findings


async def _run_trivy(args: list[str], *, timeout: int = 300) -> tuple[list[dict], str | None]:
    if not trivy_available():
        return [], "trivy not installed (see aquasecurity/trivy)"

    proc = await asyncio.create_subprocess_exec(
        "trivy", *args, "--format", "json", "--quiet",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return [], f"trivy timed out after {timeout}s"

    if proc.returncode != 0:
        return [], f"trivy exited {proc.returncode}: {err.decode()[:300]}"

    try:
        data = json.loads(out.decode() or "{}")
    except json.JSONDecodeError as e:
        return [], f"could not parse trivy output: {e}"

    return _trivy_findings_from_results(data.get("Results") or [], source_tag="trivy"), None


async def run_trivy_fs(directory: Path) -> tuple[list[dict], str | None]:
    """Filesystem/config scan of the generated artifacts directory — dependency
    manifests and IaC misconfigurations, run alongside Checkov/OPA in the
    Warden's existing pre-deploy slot."""
    return await _run_trivy(["fs", str(directory)])


async def run_trivy_image(image_tag: str) -> tuple[list[dict], str | None]:
    """CVE scan of the actual built image — only possible after Execution has
    built it, so this runs from a separate graph node, not the Warden."""
    return await _run_trivy(["image", image_tag], timeout=300)
