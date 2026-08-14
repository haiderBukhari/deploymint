"""The LangGraph StateGraph that replaces the Phase 2-4 linear driver. See
docs/09-phase-5-orchestration.md §5.1.

Scoped to the agents that exist so far (architect -> smith -> warden ->
[redteam] -> gate -> execution). Phase 6 adds oracle/finops nodes after
execution; until then a successful or failed execution goes straight to END."""

import time

from langgraph.graph import END, StateGraph

from deploymint.agents.architect import ArchitectAgent
from deploymint.agents.execution import ExecutionEngineAgent
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


async def blocked_node(state: DeployState) -> dict:
    return {"current_node": "blocked"}


def build_graph(bus=None, *, skip_deploy: bool = False):
    s = get_settings()

    g = StateGraph(DeployState)
    g.add_node("architect", _wrap(ArchitectAgent(bus)))
    g.add_node("smith", _wrap(ArtifactSmithAgent(bus)))
    g.add_node("warden", _wrap(SecurityWardenAgent(bus)))
    g.add_node("blocked", blocked_node)

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
                                {"execute": END, "blocked": "blocked"})
    else:
        g.add_node("execution", _wrap(ExecutionEngineAgent(bus)))
        g.add_conditional_edges(gate_source, security_gate,
                                {"execute": "execution", "blocked": "blocked"})
        g.add_edge("execution", END)

    g.add_edge("blocked", END)
    return g.compile()
