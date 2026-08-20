"""EventBus/BusRegistry seq continuation across paused/resumed run phases.
Regression coverage for the bug found via test_ws.py's stale-run.end test:
a resumed run reusing the same run_id with a fresh seq=0 bus collided with
already-persisted seq numbers from the paused phase, silently failing every
INSERT for the whole resumed phase. See docs/33-deploy-lock-and-findings.md."""

import pytest

from deploymint.core.events import BusRegistry, EventBus


@pytest.mark.asyncio
async def test_default_start_seq_is_zero():
    bus = EventBus("run_x")
    evt = await bus.emit("run.start")
    assert evt["seq"] == 1


@pytest.mark.asyncio
async def test_start_seq_continues_numbering_past_a_paused_phase():
    """A paused phase already persisted seq 1-5; the resumed phase's bus must
    start emitting at seq 6, not collide back at seq 1."""
    bus = EventBus("run_x", start_seq=5)
    evt = await bus.emit("run.start")
    assert evt["seq"] == 6
    evt2 = await bus.emit("node.enter")
    assert evt2["seq"] == 7


def test_registry_create_passes_start_seq_through():
    registry = BusRegistry()
    bus = registry.create("run_y", start_seq=42)
    assert bus.seq == 42
