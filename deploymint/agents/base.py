"""Every agent's contract. See docs/04-agents-spec.md."""

from abc import ABC, abstractmethod

from deploymint.agents.state import DeployState
from deploymint.core.events import EventBus


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, bus: EventBus | None = None):
        self.bus = bus

    async def emit(self, type_: str, **payload) -> None:
        if self.bus:
            await self.bus.emit(type_, payload)

    @abstractmethod
    async def run(self, state: DeployState) -> dict:
        """Return a PARTIAL state dict — only the keys this agent owns.
        Never raise: on failure append to state['errors'] and return what you have."""
