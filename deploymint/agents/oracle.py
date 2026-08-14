"""Watches a freshly deployed pod for a short window and rolls it back if it
looks unhealthy. Kubernetes-only — the docker-run fallback mode has no pod
metrics to poll, so the health check that already gated "running" is the
signal there. See docs/10-phase-6-finops-ui.md §6.1."""

import asyncio
import json

import numpy as np

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.config import get_settings
from deploymint.core import kube_engine
from deploymint.core.runner import run_command

FATAL_REASONS = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
                 "CreateContainerConfigError", "OOMKilled"}


class ObservabilityOracleAgent(BaseAgent):
    name = "oracle"

    async def run(self, state: DeployState) -> dict:
        dep = dict(state.get("deployment") or {})
        name = state["project_name"]
        if dep.get("status") != "running":
            return {}
        if dep.get("mode") != "kubernetes":
            await self.emit("oracle.done", healthy=True, samples=0, mode=dep.get("mode"))
            return {"deployment": dep}

        s = get_settings()
        samples, anomaly_reason = [], None

        for i in range(s.oracle_samples):
            snap = await self._sample(name)
            samples.append(snap)
            await self.emit("oracle.metric", **snap)

            if snap["reason"] in FATAL_REASONS:
                anomaly_reason = f"pod entered {snap['reason']}"
                break
            if snap["restarts"] > 2:
                anomaly_reason = f"pod restarted {snap['restarts']} times"
                break
            if i >= 6 and snap["ready"] == 0:
                anomaly_reason = "no ready replicas after 35s"
                break

            await asyncio.sleep(s.oracle_interval)

        if anomaly_reason is None and len(samples) >= 8:
            anomaly_reason = self._isolation_forest(samples)

        dep["metrics"] = samples
        if anomaly_reason:
            dep["anomaly_explanation"] = await self._explain(name, anomaly_reason)
            await self.emit("oracle.anomaly", reason=anomaly_reason, score=1.0,
                            explanation=dep["anomaly_explanation"])
            from deploymint.agents.remediator import RemediatorAgent

            rem = await RemediatorAgent(self.bus).run(
                {**state, "deployment": dep, "anomaly_reason": anomaly_reason})
            dep.update(rem.get("deployment", {}))
            return {"deployment": dep,
                    "errors": state.get("errors", []) + [f"oracle: {anomaly_reason}"]}

        await self.emit("oracle.done", healthy=True, samples=len(samples))
        return {"deployment": dep}

    async def _sample(self, app: str) -> dict:
        ctx = get_settings().kube_context
        ctx_flag = ["--context", ctx] if ctx else []
        r = await run_command(
            ["kubectl", *ctx_flag, "get", "pods", "-l", f"app={app}", "-o", "json"], timeout=15)
        cpu = mem = 0.0
        restarts = ready = 0
        reason = ""
        try:
            items = json.loads(r.stdout or "{}").get("items", [])
            if items:
                st = items[0].get("status", {})
                cs = (st.get("containerStatuses") or [{}])[0]
                restarts = cs.get("restartCount", 0)
                ready = 1 if cs.get("ready") else 0
                waiting = (cs.get("state", {}).get("waiting") or {})
                terminated = (cs.get("lastState", {}).get("terminated") or {})
                reason = waiting.get("reason") or terminated.get("reason") or ""
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

        top = await run_command(
            ["kubectl", *ctx_flag, "top", "pod", "-l", f"app={app}", "--no-headers"], timeout=10)
        if top.ok and top.stdout.strip():
            parts = top.stdout.split()
            if len(parts) >= 3:
                try:
                    cpu = float(parts[1].rstrip("m") or 0)
                    mem = float(parts[2].rstrip("Mi") or 0)
                except ValueError:
                    pass

        return {"cpu": cpu, "memory": mem, "restarts": restarts,
                "ready": ready, "reason": reason}

    def _isolation_forest(self, samples: list[dict]) -> str | None:
        from sklearn.ensemble import IsolationForest

        X = np.array([[s["cpu"], s["memory"], s["restarts"]] for s in samples])
        if X.std(axis=0).sum() == 0:  # perfectly flat — nothing to detect
            return None
        labels = IsolationForest(contamination=0.15, random_state=42).fit_predict(X)
        n_anom = int((labels == -1).sum())
        if n_anom >= 3:
            return f"IsolationForest flagged {n_anom}/{len(samples)} metric samples as anomalous"
        return None

    async def _explain(self, name: str, reason: str) -> str:
        """LLM anomaly explanation — runs only on the rollback path, so the
        added latency/cost is naturally rare. A failure here never blocks
        the rollback itself, and never overrides the deterministic reason."""
        from deploymint.core import llm, prompts

        try:
            pod = await kube_engine.get_pod_name(name)
            events = (await kube_engine.describe_pod(pod)).combined[-1500:] if pod else ""
            logs = (await kube_engine.pod_logs(pod)).combined[-1500:] if pod else ""
            return await llm.complete(
                system="You explain Kubernetes rollback causes in plain language.",
                user=prompts.ANOMALY_EXPLANATION_PROMPT.format(
                    reason=reason, events=events, logs=logs),
                max_tokens=200,
            )
        except Exception:
            return reason
