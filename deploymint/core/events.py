"""Per-run event bus. See docs/05-phase-1-foundation.md Step 1.5 and
docs/09-phase-5-orchestration.md §5.3 (per-client queues for multi-tab support)."""

import asyncio
from datetime import UTC, datetime
from typing import Any


class EventBus:
    """One per run. Fans events to live WebSocket clients and to the DB writer.
    Supports multiple subscribers so two browser tabs on the same run both get
    the full stream."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.seq = 0
        self._sinks: list = []  # async callables persisted by RunManager
        self._subscribers: list[asyncio.Queue] = []
        self.closed = False

    def add_sink(self, fn) -> None:
        self._sinks.append(fn)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10_000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def emit(self, type_: str, payload: dict[str, Any] | None = None) -> dict:
        self.seq += 1
        evt = {
            "run_id": self.run_id,
            "seq": self.seq,
            "type": type_,
            "payload": payload or {},
            "ts": datetime.now(UTC).isoformat(),
        }
        for sink in self._sinks:
            try:
                await sink(evt)
            except Exception:  # a broken sink must never kill a run
                pass
        for q in list(self._subscribers):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # slow consumer: drop, don't block the agent
        return evt

    async def close(self) -> None:
        self.closed = True
        for q in list(self._subscribers):
            try:
                q.put_nowait({"type": "__end__"})
            except asyncio.QueueFull:
                pass


class BusRegistry:
    """Global map of run_id -> EventBus, so the WS route can find a live run."""

    def __init__(self):
        self._buses: dict[str, EventBus] = {}

    def create(self, run_id: str) -> EventBus:
        bus = EventBus(run_id)
        self._buses[run_id] = bus
        return bus

    def get(self, run_id: str) -> EventBus | None:
        return self._buses.get(run_id)

    def drop(self, run_id: str) -> None:
        self._buses.pop(run_id, None)


registry = BusRegistry()
