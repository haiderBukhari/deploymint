"""Live event stream for a run. Replays persisted events since a client-given
seq, then tails the live queue. Uses EventBus.subscribe()/unsubscribe() (not a
single shared queue) so multiple tabs on the same run each get the full
stream. See docs/09-phase-5-orchestration.md §5.3."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from deploymint.core.cloud_jobs import registry as cloud_job_registry
from deploymint.core.events import registry
from deploymint.db.database import get_session_factory
from deploymint.db.models import Event, Run

router = APIRouter()
# "awaiting_approval" belongs here too: the graph itself has genuinely
# finished (the architect-only run completed), there's nothing left to tail
# until POST /api/runs/{id}/approve starts a new task — same shape as any
# other terminal status from this endpoint's perspective. See
# docs/33-deploy-lock-and-findings.md.
TERMINAL = {"success", "failed", "blocked", "cancelled", "awaiting_approval"}


@router.websocket("/ws/runs/{run_id}")
async def stream_run(ws: WebSocket, run_id: str):
    await ws.accept()
    Session = get_session_factory()

    since = 0
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
        since = int(first.get("since", 0))
    except Exception:
        since = 0

    # Subscribe to the live bus BEFORE querying the DB for replay. Doing it
    # in the other order — query, then subscribe — leaves a window where an
    # event emitted in between lands in neither the already-fetched replay
    # batch nor the not-yet-subscribed live queue: silently lost. Subscribing
    # first means the worst case is an event landing in *both*, which the
    # max_sent guard below dedupes instead of dropping anything.
    bus = registry.get(run_id)
    queue = bus.subscribe() if bus else None

    try:
        max_sent = since
        with Session() as db:
            rows = (db.query(Event).filter(Event.run_id == run_id, Event.seq > since)
                    .order_by(Event.seq).all())
            for r in rows:
                # run.end is a terminal MARKER, not a log line — replaying a
                # stale one from a previous phase (e.g. the architect-only
                # phase before an approval-gate resume) makes the client
                # think the run just finished again and reload, forever.
                # The one true run.end is synthesized below from the run's
                # CURRENT status instead, exactly once.
                if r.type == "run.end":
                    continue
                await ws.send_json({"seq": r.seq, "type": r.type,
                                    "payload": r.payload, "ts": r.ts.isoformat()})
                max_sent = max(max_sent, r.seq)
            run = db.get(Run, run_id)

        if run is None:
            await ws.close(code=4404)
            return

        if run.status in TERMINAL:
            await ws.send_json({"seq": -1, "type": "run.end",
                                "payload": {"status": run.status}})
            await ws.close()
            return

        if queue is None:
            # The run finished before we ever found a live bus to subscribe
            # to — nothing left to tail, and its status was already sent as
            # part of the replay above.
            await ws.close()
            return

        while True:
            evt = await queue.get()
            if evt.get("type") == "__end__":
                break
            if evt["seq"] > max_sent:
                await ws.send_json(evt)
                max_sent = evt["seq"]
    except WebSocketDisconnect:
        pass
    finally:
        if queue is not None:
            bus.unsubscribe(queue)
        try:
            await ws.close()
        except Exception:
            pass


@router.websocket("/ws/deploy/{run_id}")
async def stream_cloud_deploy(ws: WebSocket, run_id: str):
    """Live console output for a terraform plan/apply/destroy started via
    POST /api/runs/{run_id}/cloud/{action}. See core/cloud_jobs.py — this is
    a separate in-memory registry from the run's own EventBus because a
    cloud deploy is triggered well after the run (and its bus) is gone."""
    await ws.accept()
    job = cloud_job_registry.get(run_id)
    if not job:
        await ws.send_json({"type": "cloud.error", "line": "no deploy job for this run"})
        await ws.close()
        return

    for line in list(job.lines):
        await ws.send_json({"type": "cloud.line", "line": line})
    if job.status != "running":
        await ws.send_json({"type": "cloud.end", "status": job.status})
        await ws.close()
        return

    queue = job.subscribe()
    try:
        while True:
            line = await queue.get()
            if line == "__end__":
                await ws.send_json({"type": "cloud.end", "status": job.status})
                break
            await ws.send_json({"type": "cloud.line", "line": line})
    except WebSocketDisconnect:
        pass
    finally:
        job.unsubscribe(queue)
        try:
            await ws.close()
        except Exception:
            pass
