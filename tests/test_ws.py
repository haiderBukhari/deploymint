"""WebSocket streaming: a client replays persisted events since a given seq,
then gets terminal status immediately if the run already finished. See
docs/09-phase-5-orchestration.md §5.3."""

import time


def _wait_for_terminal(client, run_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = client.get(f"/api/runs/{run_id}").json()
        if got["status"] in {"success", "failed", "blocked", "cancelled"}:
            return got
        time.sleep(0.1)
    raise TimeoutError(f"run {run_id} did not reach a terminal status in {timeout}s")


def test_ws_on_a_finished_run_replays_events_and_closes(client, registered_project):
    pid = registered_project["id"]
    run_id = client.post(
        f"/api/projects/{pid}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    _wait_for_terminal(client, run_id)

    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        ws.send_json({"since": 0})
        seen_types = []
        while True:
            msg = ws.receive_json()
            seen_types.append(msg["type"])
            if msg["type"] == "run.end":
                break
    assert "run.start" in seen_types
    assert seen_types[-1] == "run.end"


def test_ws_since_filters_out_already_seen_events(client, registered_project):
    pid = registered_project["id"]
    run_id = client.post(
        f"/api/projects/{pid}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    _wait_for_terminal(client, run_id)

    all_events = []
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        ws.send_json({"since": 0})
        while True:
            msg = ws.receive_json()
            if msg["type"] == "run.end":
                break
            all_events.append(msg)

    midpoint = all_events[len(all_events) // 2]["seq"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        ws.send_json({"since": midpoint})
        replayed = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "run.end":
                break
            replayed.append(msg)

    assert all(e["seq"] > midpoint for e in replayed)
    assert len(replayed) < len(all_events)


def test_connecting_at_run_start_matches_a_full_post_hoc_replay(client, registered_project):
    """Regression test for a real race in stream_run: subscribing to the live
    bus AFTER querying the DB for replay left a window where an event
    emitted in between was neither in the already-fetched replay batch nor
    the not-yet-subscribed live queue — silently dropped. Subscribing before
    querying (and deduping on seq) means watching a run from the very start
    must see the exact same sequence of events a full replay after the run
    finishes does — no gaps, no duplicates."""
    pid = registered_project["id"]
    run_id = client.post(
        f"/api/projects/{pid}/runs", json={"skip_deploy": True}
    ).json()["run_id"]

    live_seqs = []
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        ws.send_json({"since": 0})
        while True:
            msg = ws.receive_json()
            if msg["type"] == "run.end":
                break
            live_seqs.append(msg["seq"])

    _wait_for_terminal(client, run_id)

    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        ws.send_json({"since": 0})
        replay_seqs = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "run.end":
                break
            replay_seqs.append(msg["seq"])

    assert live_seqs == replay_seqs
    assert len(live_seqs) == len(set(live_seqs)), "no event should be delivered twice"


def test_replay_never_forwards_a_stale_run_end_from_a_previous_phase(client, registered_project):
    """Regression test for a real reload-loop bug: the architecture approval
    gate pauses a run with a persisted run.end (status=awaiting_approval),
    then resumes it later under the SAME run_id. A stale run.end replayed
    from that earlier phase made the client think the (now-running-again)
    run had just finished, reload, reconnect, replay the same stale event,
    and reload again — forever. run.end must never come from replay; it's
    always synthesized fresh from the run's current status. See
    docs/33-deploy-lock-and-findings.md."""
    from deploymint.db.database import get_session_factory
    from deploymint.db.models import Event, Run

    pid = registered_project["id"]
    run_id = client.post(
        f"/api/projects/{pid}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    _wait_for_terminal(client, run_id)

    Session = get_session_factory()
    with Session() as db:
        from sqlalchemy import func

        max_seq = db.query(func.max(Event.seq)).filter_by(run_id=run_id).scalar() or 0
        # Simulate a stale run.end from a since-superseded earlier phase,
        # followed by the run being "running" again.
        db.add(Event(run_id=run_id, seq=max_seq + 1, type="run.end",
                     payload={"status": "awaiting_approval"}))
        db.query(Run).filter_by(id=run_id).update({"status": "running"})
        db.commit()

    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        ws.send_json({"since": 0})
        seen = []
        # The run is "running" (not terminal) with no live bus registered for
        # it, so the server closes without a live tail — collect everything
        # sent before that close.
        try:
            while True:
                seen.append(ws.receive_json())
        except Exception:
            pass

    run_end_events = [m for m in seen if m.get("type") == "run.end"]
    assert not run_end_events, (
        f"a stale run.end was replayed as if it were current: {run_end_events}")


def test_ws_on_unknown_run_closes(client):
    try:
        with client.websocket_connect("/ws/runs/run_doesnotexist") as ws:
            ws.receive_json()
        raised = False
    except Exception:
        raised = True
    assert raised
