"""llm_cost_usd must price a run by the model that actually ran it, not
always Claude's rate — otherwise switching DEPLOYMINT_LLM_PROVIDER (e.g. to
openai) would silently keep reporting Anthropic pricing for OpenAI tokens."""

from datetime import UTC, datetime

from deploymint.schemas.run import RunRead


def _run(model_used, input_tokens=1_000_000, output_tokens=1_000_000):
    return RunRead(
        id="r1", project_id=1, status="success", model_used=model_used,
        input_tokens=input_tokens, output_tokens=output_tokens,
        created_at=datetime.now(UTC),
    )


def test_claude_opus_5_pricing():
    assert _run("claude-opus-5").llm_cost_usd == 30.0  # $5 + $25 per 1M


def test_gpt_4o_mini_pricing_is_far_cheaper():
    assert _run("gpt-4o-mini").llm_cost_usd == 0.75  # $0.15 + $0.60 per 1M


def test_gpt_4o_pricing():
    assert _run("gpt-4o").llm_cost_usd == 12.50  # $2.50 + $10.00 per 1M


def test_gpt_4o_mini_is_not_matched_by_the_broader_gpt_4o_prefix():
    # A naive prefix check ("gpt-4o-mini".startswith("gpt-4o")) would wrongly
    # price gpt-4o-mini at gpt-4o's rate unless mini is checked first.
    assert _run("gpt-4o-mini").llm_cost_usd != _run("gpt-4o").llm_cost_usd


def test_unrecognized_model_falls_back_to_default_rate():
    assert _run("some-local-llama-model").llm_cost_usd == _run("claude-opus-5").llm_cost_usd


def test_no_tokens_means_no_cost():
    run = RunRead(id="r1", project_id=1, status="pending", created_at=datetime.now(UTC))
    assert run.llm_cost_usd is None
