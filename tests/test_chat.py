"""NL router: keyword fallback works with no ANTHROPIC_API_KEY, low-confidence
deploy intents ask for confirmation rather than acting, and a real run starts
when the model/keywords are confident. See docs/09-phase-5-orchestration.md §5.4."""

from unittest.mock import patch

import pytest


def test_deploy_with_no_project_asks_which_one(client):
    r = client.post("/api/chat", json={"message": "please deploy"})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "deploy"
    assert body["action_taken"] == "none"
    assert body["run_id"] is None


def test_empty_message_is_400(client):
    r = client.post("/api/chat", json={"message": "  "})
    assert r.status_code == 400


def test_unknown_intent_does_not_act(client):
    r = client.post("/api/chat", json={"message": "what's the weather like"})
    body = r.json()
    assert body["action_taken"] == "none"
    assert body["run_id"] is None


@pytest.mark.asyncio
async def test_keyword_fallback_classifies_without_any_llm_call(client, registered_project):
    """No ANTHROPIC_API_KEY is set — classify() must fall back to keyword
    matching. The keyword path's confidence (0.6) is below the 0.8 threshold
    that's allowed to start a deploy unconfirmed, so this asks rather than
    acts — the safety check applies uniformly, not just to the LLM path."""
    r = client.post(
        "/api/chat",
        json={"message": "deploy test-app now", "project_id": registered_project["id"]},
    )
    body = r.json()
    assert body["intent"] == "deploy"
    assert body["action_taken"] == "confirm_required"
    assert body["pending"]["project_id"] == registered_project["id"]


@pytest.mark.asyncio
async def test_high_confidence_llm_classification_starts_a_run(client, registered_project):
    """Routes through the same start_run() every other trigger uses — mocked
    here so this test doesn't also need a real Docker build (that path is
    covered by test_execution.py and test_graph.py already)."""
    llm_reply = (
        '{"intent": "deploy", "project": "test-app", '
        '"params": {"replicas": null, "force": false, "env": null}, "confidence": 0.95}'
    )
    with patch("deploymint.core.llm.complete", return_value=llm_reply), \
         patch("deploymint.api.chat.start_run", return_value="run_mocked123") as mock_start:
        r = client.post("/api/chat", json={"message": "deploy test-app right now"})
    body = r.json()
    assert body["action_taken"] == "run_started"
    assert body["run_id"] == "run_mocked123"
    mock_start.assert_called_once()
    assert mock_start.call_args.kwargs["trigger"] == "chat"


@pytest.mark.asyncio
async def test_low_confidence_deploy_asks_for_confirmation(client, registered_project):
    llm_reply = (
        '{"intent": "deploy", "project": "test-app", '
        '"params": {"replicas": null, "force": false, "env": null}, "confidence": 0.4}'
    )
    with patch("deploymint.core.llm.complete", return_value=llm_reply):
        r = client.post("/api/chat", json={"message": "maybe deploy test-app?"})
    body = r.json()
    assert body["action_taken"] == "confirm_required"
    assert body["run_id"] is None
    assert body["pending"]["project_id"] == registered_project["id"]
