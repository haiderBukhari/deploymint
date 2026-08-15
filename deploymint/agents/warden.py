"""Security Warden: Checkov + OPA gate. See docs/07-phase-3-security.md §3.6
and §4.3b (finding explanations)."""

import asyncio

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.config import get_settings
from deploymint.core import llm, prompts, scanners
from deploymint.core.artifact_store import write_artifacts

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
EXPLAIN_SEVERITIES = {"critical", "high"}


async def _explain(finding: dict) -> str:
    """One-sentence plain-language risk explanation for a single finding. Never
    raises — a failure here just means no explanation, never a blocked run or
    a crashed agent. Capped to critical/high findings so cost and latency stay
    small and predictable (see docs/07-phase-3-security.md §4.3b)."""
    try:
        return await llm.complete(
            system="You explain security findings in plain language.",
            user=prompts.FINDING_EXPLANATION_PROMPT.format(
                id=finding["id"], message=finding["message"]),
            max_tokens=100,
        )
    except Exception:
        return ""


async def _add_explanations(findings: list[dict]) -> None:
    targets = [f for f in findings if f["severity"] in EXPLAIN_SEVERITIES]
    if not targets:
        return
    explanations = await asyncio.gather(*(_explain(f) for f in targets))
    for f, explanation in zip(targets, explanations, strict=True):
        if explanation:
            f["explanation"] = explanation.strip()


class SecurityWardenAgent(BaseAgent):
    name = "warden"

    async def run(self, state: DeployState) -> dict:
        s = get_settings()
        artifacts = state.get("artifacts") or {}
        directory = write_artifacts(state["run_id"], state["repo_path"], artifacts)

        ck_findings, ck_err = await scanners.run_checkov(directory)
        opa_findings, opa_err = await scanners.run_opa(artifacts)

        findings = ck_findings + opa_findings
        await _add_explanations(findings)
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
