"""Red Team: deterministic probes (always run, block on their own) plus an
optional LLM adversarial critique layer. See docs/07-phase-3-security.md §3.7
and docs/04-agents-spec.md §4.4."""

import re

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.config import get_settings
from deploymint.core import llm, prompts

PROBES = [
    ("RT_CURL_PIPE_SH", "critical",
     r"(curl|wget)[^\n|]*\|\s*(ba)?sh",
     "Remote script piped directly to a shell — classic supply-chain injection."),

    ("RT_UNPINNED_BASE", "high",
     r"^FROM\s+\S+:latest",
     "Base image pinned to :latest — build is not reproducible."),

    ("RT_NO_DIGEST", "low",
     r"^FROM\s+(?!.*@sha256:)",
     "Base image not pinned by digest."),

    ("RT_PRIVILEGED", "critical",
     r"privileged:\s*true",
     "Privileged container requested — full host access."),

    ("RT_HOST_NETWORK", "critical",
     r"hostNetwork:\s*true",
     "Pod shares the host network namespace."),

    ("RT_HOST_PATH", "critical",
     r"hostPath:",
     "hostPath volume mounts the host filesystem into the pod."),

    ("RT_DOCKER_SOCK", "critical",
     r"/var/run/docker\.sock",
     "Docker socket mounted — equivalent to root on the host."),

    ("RT_HARDCODED_SECRET", "critical",
     r"(?i)(password|passwd|secret|api[_-]?key|token|aws_access_key)\s*[=:]\s*['\"]?[A-Za-z0-9/+_-]{8,}",
     "Possible hardcoded credential."),

    ("RT_SUSPICIOUS_EGRESS", "high",
     r"(?i)(nc|netcat|ncat)\s+-[a-z]*e|bash\s+-i\s+>&\s*/dev/tcp",
     "Reverse-shell pattern."),

    ("RT_ADD_REMOTE", "medium",
     r"^ADD\s+https?://",
     "ADD with a remote URL — use COPY, or curl with a checksum."),

    ("RT_SUDO_INSTALL", "medium",
     r"apt-get install.*sudo|yum install.*sudo",
     "sudo installed in a container — unnecessary privilege escalation surface."),
]

# An LLM alone never blocks a deploy — a strong model is still a model, and the
# trust boundary in 01-architecture.md §1.9 (LLM writes/explains, deterministic
# code decides) is what this cap enforces in code. Deterministic probes above
# keep their stated severity regardless.
LLM_SEVERITY_CAP = {"critical": "high"}


class RedTeamAgent(BaseAgent):
    name = "redteam"

    async def run(self, state: DeployState) -> dict:
        artifacts = state.get("artifacts") or {}
        security = dict(state.get("security") or {"passed": True, "findings": []})
        blob = "\n".join(str(artifacts.get(k, "")) for k in
                         ("dockerfile", "k8s_deployment", "k8s_service"))

        findings = self._deterministic_probes(blob)
        for f in findings:
            await self.emit("redteam.probe", probe_name=f["id"], result="hit")

        if get_settings().enable_redteam:
            try:
                findings += await self._llm_probe(artifacts, state.get("analysis") or {})
            except Exception as e:
                findings.append({
                    "id": "RT_LLM_UNAVAILABLE", "severity": "info", "source": "redteam",
                    "file": "-", "message": f"LLM red team skipped: {str(e)[:120]}",
                    "remediation": "Deterministic probes still ran.",
                })

        security["findings"] = list(security.get("findings", [])) + findings
        security["redteam_ran"] = True
        criticals = [f for f in findings if f["severity"] == "critical"]
        if criticals:
            security["passed"] = False
            crit = criticals[0]
            security["blocked_reason"] = f"Red Team: [{crit['id']}] {crit['message']}"

        await self.emit("redteam.done", findings_count=len(findings))
        return {"security": security}

    def _deterministic_probes(self, blob: str) -> list[dict]:
        findings = []
        for pid, severity, pattern, message in PROBES:
            flags = re.IGNORECASE | re.MULTILINE
            if re.search(pattern, blob, flags):
                findings.append({
                    "id": pid, "severity": severity, "source": "redteam",
                    "file": "-", "message": message,
                    "remediation": "Remove or replace the flagged pattern.",
                })
        return findings

    async def _llm_probe(self, artifacts: dict, analysis: dict) -> list[dict]:
        user = (
            f"Repository analysis:\n{analysis}\n\n"
            f"Dockerfile:\n{artifacts.get('dockerfile', '')}\n\n"
            f"K8s Deployment:\n{artifacts.get('k8s_deployment', '')}\n\n"
            f"K8s Service:\n{artifacts.get('k8s_service', '')}\n"
        )
        data = await llm.complete_json(prompts.REDTEAM_SYSTEM, user)
        out = []
        for f in data.get("findings", []):
            f = dict(f)
            f["severity"] = LLM_SEVERITY_CAP.get(f.get("severity"), f.get("severity", "low"))
            f["source"] = "redteam"
            f.setdefault("file", "-")
            f.setdefault("remediation", "")
            out.append(f)
        return out
