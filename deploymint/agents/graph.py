"""The full LangGraph StateGraph: architect -> smith -> warden -> [redteam] ->
gate -> execution -> [oracle -> remediator] -> finops -> END. See
docs/09-phase-5-orchestration.md §5.1 and docs/10-phase-6-finops-ui.md §6.1-6.3."""

import time

from langgraph.graph import END, StateGraph

from deploymint.agents.architect import ArchitectAgent
from deploymint.agents.execution import ExecutionEngineAgent
from deploymint.agents.finops import FinOpsAgent
from deploymint.agents.oracle import ObservabilityOracleAgent
from deploymint.agents.redteam import RedTeamAgent
from deploymint.agents.smith import ArtifactSmithAgent
from deploymint.agents.state import DeployState
from deploymint.agents.warden import SecurityWardenAgent
from deploymint.config import get_settings


def _wrap(agent):
    """Adapt a BaseAgent into a LangGraph node with timing + enter/exit events."""

    async def node(state: DeployState) -> dict:
        await agent.emit("node.enter", node=agent.name)
        t0 = time.perf_counter()
        try:
            result = await agent.run(state)
        except Exception as e:  # a node must never kill the graph
            await agent.emit("error", node=agent.name, message=str(e)[:500])
            result = {"errors": state.get("errors", []) + [f"{agent.name}: {str(e)[:300]}"]}
        ms = int((time.perf_counter() - t0) * 1000)
        await agent.emit("node.exit", node=agent.name, ms=ms)
        return {**result, "current_node": agent.name}

    node.__name__ = f"{agent.name}_node"
    return node


def security_gate(state: DeployState) -> str:
    sec = state.get("security") or {}
    if state.get("force"):
        return "execute"
    return "execute" if sec.get("passed") else "blocked"


def post_execution(state: DeployState) -> str:
    dep = state.get("deployment") or {}
    return "observe" if dep.get("status") == "running" else "finops"


async def blocked_node(state: DeployState) -> dict:
    return {"current_node": "blocked"}


def build_graph(bus=None, *, skip_deploy: bool = False):
    s = get_settings()

    g = StateGraph(DeployState)
    g.add_node("architect", _wrap(ArchitectAgent(bus)))
    g.add_node("smith", _wrap(ArtifactSmithAgent(bus)))
    g.add_node("warden", _wrap(SecurityWardenAgent(bus)))
    g.add_node("blocked", blocked_node)
    g.add_node("finops", _wrap(FinOpsAgent(bus)))

    g.set_entry_point("architect")
    g.add_edge("architect", "smith")
    g.add_edge("smith", "warden")

    if s.enable_redteam:
        g.add_node("redteam", _wrap(RedTeamAgent(bus)))
        g.add_edge("warden", "redteam")
        gate_source = "redteam"
    else:
        gate_source = "warden"

    if skip_deploy:
        g.add_conditional_edges(gate_source, security_gate,
                                {"execute": "finops", "blocked": "blocked"})
    else:
        g.add_node("execution", _wrap(ExecutionEngineAgent(bus)))
        g.add_node("oracle", _wrap(ObservabilityOracleAgent(bus)))
        g.add_conditional_edges(gate_source, security_gate,
                                {"execute": "execution", "blocked": "blocked"})
        g.add_conditional_edges("execution", post_execution,
                                {"observe": "oracle", "finops": "finops"})
        g.add_edge("oracle", "finops")

    g.add_edge("finops", END)
    g.add_edge("blocked", END)
    return g.compile()
