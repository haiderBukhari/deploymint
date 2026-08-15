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
