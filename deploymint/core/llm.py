"""Claude client wrapper. See docs/06-phase-2-generation.md §2.2 and
docs/04-agents-spec.md §4.10 (LLM usage policy)."""

import asyncio
import json
import re

import anthropic

from deploymint.config import get_settings

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        s = get_settings()
        if not s.anthropic_api_key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    return _client


async def health() -> tuple[bool, str]:
    try:
        client = get_client()
    except LLMUnavailable as e:
        return False, str(e)
    try:
        await asyncio.to_thread(
            client.messages.create,
            model=get_settings().model, max_tokens=8,
            messages=[{"role": "user", "content": "ok"}],
        )
        return True, f"{get_settings().model} reachable"
    except anthropic.AuthenticationError:
        return False, "ANTHROPIC_API_KEY is set but invalid"
    except anthropic.APIConnectionError as e:
        return False, f"cannot reach the Anthropic API: {e}"
    except Exception as e:
        return False, str(e)[:200]


async def complete(system: str, user: str, *, max_tokens: int = 4000,
                    temperature: float = 0.1, json_mode: bool = False) -> str:
    """Single completion. Runs the blocking SDK call in a thread so it never
    freezes the event loop — see docs/01-architecture.md §1.6."""
    s = get_settings()
    client = get_client()

    sys_prompt = system
    if json_mode:
        sys_prompt += "\n\nReturn ONLY a JSON object. No markdown fences, no prose."

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.messages.create,
                model=s.model, max_tokens=max_tokens, temperature=temperature,
                system=sys_prompt,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=s.llm_timeout,
        )
    except anthropic.RateLimitError as e:
        raise LLMUnavailable(f"rate limited: {e}") from e
    except anthropic.APIConnectionError as e:
        raise LLMUnavailable(f"cannot reach the API: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"api error {e.status_code}: {e.message}") from e
    except TimeoutError as e:
        raise LLMError(f"timed out after {s.llm_timeout}s") from e

    if response.stop_reason == "refusal":
        raise LLMError("model declined the request")
    return response.content[0].text


def extract_json(text: str) -> dict:
    """Even a strong model occasionally wraps JSON in fences or a sentence of
    prose. Dig it out rather than trying to prompt-engineer this away entirely."""
    text = text.strip()
    m = FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


async def complete_json(system: str, user: str, **kw) -> dict:
    raw = await complete(system, user, json_mode=True, **kw)
    return extract_json(raw)
