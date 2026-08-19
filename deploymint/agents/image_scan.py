"""Post-Execution image scan: real CVEs in the actual built image, not just
the generated files. Runs after Execution because the image only exists
then — a different graph position than the Warden's pre-deploy slot. Never
blocks the deploy (it already happened); findings surface on the run page
for the next run/fix cycle instead. See docs/30-trivy.md."""

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.config import get_settings
from deploymint.core import scanners


class ImageScanAgent(BaseAgent):
    name = "image_scan"

    async def run(self, state: DeployState) -> dict:
        security = dict(state.get("security") or {"passed": True, "findings": []})
        deployment = state.get("deployment") or {}
        image_tag = deployment.get("image_tag", "")

        if not get_settings().enable_trivy or not image_tag:
            return {}

        findings, err = await scanners.run_trivy_image(image_tag)
        security["findings"] = list(security.get("findings", [])) + findings
        security["trivy_image_ran"] = err is None
        for f in findings:
            await self.emit("warden.finding", **f)

        await self.emit("image_scan.done", ran=err is None, findings_count=len(findings),
                        error=err)
        return {"security": security}
