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


def test_ws_on_unknown_run_closes(client):
    try:
        with client.websocket_connect("/ws/runs/run_doesnotexist") as ws:
            ws.receive_json()
        raised = False
    except Exception:
        raised = True
    assert raised
