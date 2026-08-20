"""Code Audit: an LLM-driven checklist over the user's ACTUAL source code —
not the generated Dockerfile/K8s/Terraform Red Team already probes. Replaces
a dependency-CVE scanner (Trivy, then Grype) that was tried and dropped: both
needed a large vulnerability database to download before the first scan
could run at all, and even bootstrapped, returned 200+ findings that were
almost entirely low-severity noise in base OS packages nobody could act on.
See docs/34-code-audit.md and docs/32-redteam-fixes.md (the shared severity
clamp this agent also uses).
"""

from pathlib import Path

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.agents.warden import SEVERITY_ORDER, clamp_llm_severity
from deploymint.config import get_settings
from deploymint.core import llm, prompts
from deploymint.core.repo_scanner import walk_repo

# Extensions relevant to this checklist that repo_scanner's own
# EXTENSION_LANGUAGE map doesn't cover (that map is for language DETECTION,
# not for "what should a secrets/auth/injection audit read"). Config and env
# files matter here even though they aren't "source code" in that sense.
AUDIT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".php",
    ".rs", ".env", ".yaml", ".yml", ".json", ".ini", ".cfg", ".toml",
}
# Filenames worth reading regardless of extension — where hardcoded secrets
# and insecure defaults live most often.
AUDIT_FILENAMES = {".env", ".env.example", ".env.local", "config.py", "settings.py",
                   "docker-compose.yml", "docker-compose.yaml"}

# Total character budget across all files sent to the LLM — sending every
# file's full content on a real repo would blow the token budget. No prior
# art in this codebase for this kind of multi-file budget (see
# docs/34-code-audit.md); files past the budget are skipped and logged, never
# silently dropped with a false claim of full coverage.
MAX_AUDIT_CHARS = 60_000
MAX_FILES_READ = 40


def _relevant_files(root: Path, files: list[Path]) -> list[Path]:
    return [
        f for f in files
        if f.suffix in AUDIT_EXTENSIONS or f.name in AUDIT_FILENAMES
    ]


def _order_by_criticality(files: list[Path], root: Path, critical_files: list[str]) -> list[Path]:
    """Critical files (already PageRank-ranked by the Architect, same list
    smith.py prioritizes) go first — if the budget runs out, it runs out on
    the least-central files, not an arbitrary filesystem order."""
    critical_set = set(critical_files)

    def rel(f: Path) -> str:
        try:
            return str(f.relative_to(root))
        except ValueError:
            return str(f)

    critical = [f for f in files if rel(f) in critical_set]
    rest = [f for f in files if rel(f) not in critical_set]
    return critical + rest


class CodeAuditAgent(BaseAgent):
    name = "code_audit"

    async def run(self, state: DeployState) -> dict:
        security = dict(state.get("security") or {"passed": True, "findings": []})

        if not get_settings().enable_code_audit:
            return {}

        root = Path(state["repo_path"])
        analysis = state.get("analysis") or {}

        try:
            findings = await self._audit(root, analysis)
        except Exception as e:
            findings = [{
                "id": "CA_LLM_UNAVAILABLE", "severity": "info", "source": "code_audit",
                "file": "-", "message": f"Code audit skipped: {str(e)[:120]}",
                "remediation": "No source-code audit ran for this deploy.",
            }]

        security["findings"] = list(security.get("findings", [])) + findings
        security["code_audit_ran"] = True
        # warden.py only counts its own findings — recompute now that this
        # agent's findings have been appended, same gotcha redteam.py already
        # documents.
        security["counts"] = {
            lvl: sum(1 for f in security["findings"] if f["severity"] == lvl)
            for lvl in SEVERITY_ORDER
        }
        criticals = [f for f in findings if f["severity"] == "critical"]
        if criticals:
            security["passed"] = False
            crit = criticals[0]
            security["blocked_reason"] = f"Code Audit: [{crit['id']}] {crit['message']}"

        await self.emit("code_audit.done", findings_count=len(findings))
        return {"security": security}

    async def _audit(self, root: Path, analysis: dict) -> list[dict]:
        scan = walk_repo(root)
        candidates = _order_by_criticality(
            _relevant_files(root, scan.files), root, analysis.get("critical_files") or [])

        blocks, skipped, used_chars = [], 0, 0
        for f in candidates[:MAX_FILES_READ]:
            try:
                content = f.read_text(errors="ignore")
            except OSError:
                continue
            if used_chars + len(content) > MAX_AUDIT_CHARS:
                skipped += 1
                continue
            used_chars += len(content)
            try:
                rel = str(f.relative_to(root))
            except ValueError:
                rel = str(f)
            blocks.append(f"--- {rel} ---\n{content}")

        if not blocks:
            return []

        user = (
            f"Dependencies: {analysis.get('dependencies') or []}\n\n"
            + "\n\n".join(blocks)
            + (f"\n\n({skipped} additional file(s) skipped — over the audit budget.)"
               if skipped else "")
        )
        data = await llm.complete_json(prompts.CODE_AUDIT_SYSTEM, user)

        out = []
        for f in data.get("findings", []):
            f = dict(f)
            f["severity"] = clamp_llm_severity(f.get("severity"))
            f["source"] = "code_audit"
            f.setdefault("file", "-")
            f.setdefault("remediation", "")
            out.append(f)
        return out
