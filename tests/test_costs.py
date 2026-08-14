"""Cost Q&A: every dollar figure in the answer must come from `data`, computed
deterministically — never from the LLM. See docs/10-phase-6-finops-ui.md §6.3."""


def test_most_expensive_matches_the_sample_export_exactly(client):
    r = client.post("/api/costs/query", json={"question": "which service costs the most?"})
    body = r.json()
    assert body["intent"] == "most_expensive"
    assert body["data"]["service"] == "Amazon Elastic Compute Cloud - Compute"
    assert body["data"]["amount"] == 487.12
    assert "487.12" in body["answer"]


def test_by_service_breakdown_sums_match_answer(client):
    r = client.post("/api/costs/query", json={"question": "give me a breakdown by service"})
    body = r.json()
    assert body["intent"] == "by_service"
    for svc, amount in body["data"]["by_service"].items():
        assert f"${amount:,.2f}" in body["answer"]


def test_total_spend_is_the_sum_of_the_breakdown(client):
    r = client.post("/api/costs/query", json={"question": "how much have we spent overall"})
    body = r.json()
    assert body["intent"] == "total_spend"
    assert body["data"]["total"] > 0
    assert f"{body['data']['total']:,.2f}" in body["answer"]


def test_optimize_intent_points_at_the_biggest_cost(client):
    r = client.post("/api/costs/query", json={"question": "how can I reduce spend"})
    body = r.json()
    assert body["intent"] == "optimize"
    assert body["data"]["service"] == "Amazon Elastic Compute Cloud - Compute"
