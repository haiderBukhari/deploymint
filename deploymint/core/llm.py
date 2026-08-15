"""Dispatches to whichever provider Settings.llm_provider selects — the
public surface here (complete/complete_json/health/extract_json/LLMError/
LLMUnavailable) is unchanged from before the provider seam existed, so every
call site (Smith, Red Team, Chat, Oracle, Warden) needed zero changes.
See docs/06-phase-2-generation.md §2.2, docs/04-agents-spec.md §4.10, and
docs/17-pending-work.md §17.6."""

import json
import re

from deploymint.config import get_settings
from deploymint.core.providers.base import LLMError, LLMUnavailable, Provider

__all__ = ["LLMError", "LLMUnavailable", "complete", "complete_json",
           "extract_json", "get_provider", "health"]

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_provider: Provider | None = None
_provider_key: tuple | None = None


def get_provider() -> Provider:
    """Cached by the settings that actually determine provider identity —
    changing the model/provider/key/base-url via Settings (e.g. in tests, or
    a live config change) transparently produces a fresh provider instead of
    silently reusing a stale one."""
    global _provider, _provider_key
    s = get_settings()
    key = (s.llm_provider, s.model, s.llm_base_url, s.anthropic_api_key, s.openai_api_key)
    if _provider is None or _provider_key != key:
        if s.llm_provider in ("openai", "openai_compatible"):
            from deploymint.core.providers.openai_compatible_provider import (
                OpenAICompatibleProvider,
            )

            if s.llm_provider == "openai":
                # The real API — same chat-completions shape as the local
                # runtimes "openai_compatible" targets, just a fixed URL and
                # OPENAI_API_KEY instead of a self-hosted base_url.
                base_url = s.llm_base_url or "https://api.openai.com/v1"
                api_key = s.openai_api_key
            else:
                base_url, api_key = s.llm_base_url, s.anthropic_api_key
            _provider = OpenAICompatibleProvider(base_url, s.model, api_key)
        else:
            from deploymint.core.providers.anthropic_provider import AnthropicProvider

            _provider = AnthropicProvider(s.anthropic_api_key, s.model)
        _provider_key = key
    return _provider


async def health() -> tuple[bool, str]:
    return await get_provider().health()


async def complete(system: str, user: str, *, max_tokens: int = 4000,
                    temperature: float = 0.1, json_mode: bool = False) -> str:
    """Single completion. The provider runs any blocking SDK call in a thread
    so it never freezes the event loop — see docs/01-architecture.md §1.6."""
    s = get_settings()
    sys_prompt = system
    if json_mode:
        sys_prompt += "\n\nReturn ONLY a JSON object. No markdown fences, no prose."
    return await get_provider().complete_raw(
        sys_prompt, user, max_tokens=max_tokens, temperature=temperature, timeout=s.llm_timeout)


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
