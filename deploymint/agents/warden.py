"""Security Warden: Checkov + OPA gate. See docs/07-phase-3-security.md §3.6."""

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.config import get_settings
from deploymint.core import scanners
from deploymint.core.artifact_store import write_artifacts

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


class SecurityWardenAgent(BaseAgent):
    name = "warden"

    async def run(self, state: DeployState) -> dict:
        s = get_settings()
        artifacts = state.get("artifacts") or {}
        directory = write_artifacts(state["run_id"], state["repo_path"], artifacts)

        ck_findings, ck_err = await scanners.run_checkov(directory)
        opa_findings, opa_err = await scanners.run_opa(artifacts)

        findings = ck_findings + opa_findings
        for f in findings:
            await self.emit("warden.finding", **f)

        checkov_ran, opa_ran = ck_err is None, opa_err is None
        if not checkov_ran and not opa_ran:
            report = {
                "passed": False, "findings": findings,
                "checkov_ran": False, "opa_ran": False, "redteam_ran": False,
                "blocked_reason": "No security scanner available — failing closed. "
                                  f"checkov: {ck_err}; opa: {opa_err}",
            }
            await self.emit("warden.done", passed=False, critical=0, high=0, medium=0, low=0)
            return {"security": report}

        threshold = s.block_severity
        blocking_levels = set(SEVERITY_ORDER[: SEVERITY_ORDER.index(threshold) + 1])
        blockers = [f for f in findings if f["severity"] in blocking_levels]
        passed = not blockers

        counts = {lvl: sum(1 for f in findings if f["severity"] == lvl) for lvl in SEVERITY_ORDER}
        report = {
            "passed": passed, "findings": findings,
            "checkov_ran": checkov_ran, "opa_ran": opa_ran, "redteam_ran": False,
        }
        if not passed:
            top = blockers[0]
            report["blocked_reason"] = (
                f"{len(blockers)} {threshold}-or-above finding(s). "
                f"First: [{top['id']}] {top['message']}"
            )

        await self.emit("warden.done", passed=passed, **counts)
        return {"security": report}
