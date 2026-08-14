"""FinOps: estimates monthly cost from the generated K8s manifest's resource
requests. The LLM never computes a number — this agent is pure deterministic
arithmetic against a rate card. See docs/10-phase-6-finops-ui.md §6.3."""

import json
from importlib.resources import files

import yaml

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState

HOURS_PER_MONTH = 730


def load_rate_card(cloud: str = "aws") -> dict:
    data = json.loads((files("deploymint") / "data/rate_card.json").read_text())
    return data.get(cloud, data["aws"])


def parse_quantity(q, kind: str) -> float:
    """K8s quantities: '500m' cpu -> 0.5 vCPU; '512Mi' memory -> 0.5 GB."""
    if not q:
        return 0.0
    q = str(q)
    if kind == "cpu":
        return float(q[:-1]) / 1000 if q.endswith("m") else float(q)
    for suffix, mult in (("Gi", 1.0), ("Mi", 1 / 1024), ("Ki", 1 / 1024**2),
                         ("G", 1e9 / 1024**3), ("M", 1e6 / 1024**3)):
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * mult
    return float(q) / 1024**3


class FinOpsAgent(BaseAgent):
    name = "finops"

    async def run(self, state: DeployState) -> dict:
        artifacts = state.get("artifacts") or {}
        report = self._estimate(artifacts)
        await self.emit("finops.done", **report)
        return {"cost": report}

    def _estimate(self, artifacts: dict) -> dict:
        rates = load_rate_card("aws")
        try:
            dep = yaml.safe_load(artifacts.get("k8s_deployment", "")) or {}
            replicas = dep.get("spec", {}).get("replicas", 1)
            containers = dep["spec"]["template"]["spec"]["containers"]
        except Exception:
            return {"source": "estimate", "monthly_usd": 0.0, "breakdown": {},
                    "recommendations": ["Could not parse the Deployment manifest."]}

        total, breakdown, recs = 0.0, {}, []
        for c in containers:
            res = c.get("resources", {}) or {}
            req, lim = res.get("requests", {}) or {}, res.get("limits", {}) or {}
            cpu = parse_quantity(req.get("cpu"), "cpu")
            mem = parse_quantity(req.get("memory"), "mem")
            cost = (cpu * rates["vcpu_hour"] + mem * rates["gb_hour"]) * HOURS_PER_MONTH * replicas
            breakdown[c.get("name", "container")] = round(cost, 2)
            total += cost

            lim_cpu = parse_quantity(lim.get("cpu"), "cpu")
            if cpu and lim_cpu and lim_cpu / cpu > 4:
                recs.append(f"'{c.get('name')}': CPU limit is {lim_cpu/cpu:.0f}x the "
                            "request — likely over-provisioned or under-requested.")
            if not lim:
                recs.append(f"'{c.get('name')}': no resource limits — cost is unbounded.")
            if mem > 1.0:
                recs.append(f"'{c.get('name')}': {mem:.1f} GB memory request. "
                            f"Halving it saves ~${mem/2 * rates['gb_hour'] * HOURS_PER_MONTH:.2f}/mo.")

        if replicas == 1:
            recs.append(f"Single replica — no high availability. "
                        f"A second replica costs ~${total:.2f}/mo more.")
        recs.append("No HorizontalPodAutoscaler configured — "
                    "scaling to zero off-peak could cut this substantially.")

        return {"source": "estimate", "monthly_usd": round(total, 2),
                "breakdown": breakdown, "recommendations": recs[:5]}
