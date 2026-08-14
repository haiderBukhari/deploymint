"""Rolls back a deployment the Oracle flagged as unhealthy. Handles the
no-previous-revision case (a fresh project's first deploy) by deleting the
deployment instead of failing the rollback. See docs/10-phase-6-finops-ui.md §6.2."""

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.core import kube_engine


class RemediatorAgent(BaseAgent):
    name = "remediator"

    async def run(self, state: DeployState) -> dict:
        name = state["project_name"]
        dep = dict(state.get("deployment") or {})
        reason = state.get("anomaly_reason", "anomaly detected")

        await self.emit("remediator.start", reason=reason)

        r = await kube_engine.rollout_undo(name)
        if r.ok:
            await kube_engine.rollout_status(name)
            dep["status"] = "rolled_back"
            dep["remediation"] = f"rolled back to previous revision ({reason})"
        else:
            # First-ever deploy has no previous revision — remove it instead.
            d = await kube_engine.delete_deployment(name)
            dep["status"] = "rolled_back"
            dep["remediation"] = (
                f"no previous revision; deployment removed ({reason}). "
                f"{'' if d.ok else 'Manual cleanup may be required.'}"
            )

        await self.emit("remediator.done", status=dep["status"], detail=dep["remediation"])
        return {"deployment": dep}
