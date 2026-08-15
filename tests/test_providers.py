"""The provider seam: core/llm.py's public surface must stay identical
regardless of which provider answers, and each provider must degrade the
same way (LLMUnavailable for unreachable, LLMError for a bad response) so
callers like agents/smith.py don't need to know which one is active.
See docs/17-pending-work.md §17.6."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from deploymint.core import llm
from deploymint.core.providers.base import LLMError, LLMUnavailable
from deploymint.core.providers.openai_compatible_provider import OpenAICompatibleProvider


def test_default_provider_is_anthropic(monkeypatch):
    from deploymint.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_settings.cache_clear()
    llm._provider = None
    llm._provider_key = None
    provider = llm.get_provider()
    assert type(provider).__name__ == "AnthropicProvider"
    get_settings.cache_clear()


def test_switching_provider_via_settings_produces_a_fresh_instance(monkeypatch):
    from deploymint.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_settings.cache_clear()
    llm._provider = None
    llm._provider_key = None
    first = llm.get_provider()

    monkeypatch.setenv("DEPLOYMINT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DEPLOYMINT_LLM_BASE_URL", "http://localhost:11434/v1")
    get_settings.cache_clear()
    second = llm.get_provider()

    assert type(first).__name__ == "AnthropicProvider"
    assert type(second).__name__ == "OpenAICompatibleProvider"
    get_settings.cache_clear()


def test_openai_provider_defaults_to_the_real_api_url(monkeypatch):
    """DEPLOYMINT_LLM_PROVIDER=openai reuses OpenAICompatibleProvider (same
    chat-completions shape as the real API) but defaults its base_url to
    OpenAI's actual endpoint and reads OPENAI_API_KEY, not ANTHROPIC_API_KEY."""
    from deploymint.config import get_settings

    monkeypatch.setenv("DEPLOYMINT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
    monkeypatch.delenv("DEPLOYMINT_LLM_BASE_URL", raising=False)
    get_settings.cache_clear()
    llm._provider = None
    llm._provider_key = None

    provider = llm.get_provider()
    assert type(provider).__name__ == "OpenAICompatibleProvider"
    assert provider._base_url == "https://api.openai.com/v1"
    assert provider._api_key == "sk-oai-test"

    get_settings.cache_clear()
    llm._provider = None
    llm._provider_key = None


def test_openai_provider_respects_an_explicit_base_url_override(monkeypatch):
    """E.g. an Azure OpenAI or OpenAI-proxy deployment."""
    from deploymint.config import get_settings

    monkeypatch.setenv("DEPLOYMINT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
    monkeypatch.setenv("DEPLOYMINT_LLM_BASE_URL", "https://my-proxy.example.com/v1")
    get_settings.cache_clear()
    llm._provider = None
    llm._provider_key = None

    provider = llm.get_provider()
    assert provider._base_url == "https://my-proxy.example.com/v1"

    get_settings.cache_clear()
    llm._provider = None
    llm._provider_key = None
    llm._provider = None
    llm._provider_key = None


@pytest.mark.asyncio
async def test_openai_compatible_provider_success():
    provider = OpenAICompatibleProvider("http://localhost:11434/v1", "llama3.1")
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]
    }
    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=fake_response)):
        out = await provider.complete_raw(
            "sys", "user", max_tokens=100, temperature=0.1, timeout=30)
    assert out == "hello"


@pytest.mark.asyncio
async def test_openai_compatible_provider_connect_error_is_unavailable():
    provider = OpenAICompatibleProvider("http://localhost:11434/v1", "llama3.1")
    with patch.object(httpx.AsyncClient, "post",
                      new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
        with pytest.raises(LLMUnavailable):
            await provider.complete_raw("sys", "user", max_tokens=100, temperature=0.1, timeout=30)


@pytest.mark.asyncio
async def test_openai_compatible_provider_error_status_is_llmerror():
    provider = OpenAICompatibleProvider("http://localhost:11434/v1", "llama3.1")
    fake_response = MagicMock(status_code=500, text="internal error")
    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=fake_response)):
        with pytest.raises(LLMError):
            await provider.complete_raw("sys", "user", max_tokens=100, temperature=0.1, timeout=30)


@pytest.mark.asyncio
async def test_openai_compatible_provider_rate_limit_is_unavailable():
    provider = OpenAICompatibleProvider("http://localhost:11434/v1", "llama3.1")
    fake_response = MagicMock(status_code=429, text="too many requests")
    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=fake_response)):
        with pytest.raises(LLMUnavailable):
            await provider.complete_raw("sys", "user", max_tokens=100, temperature=0.1, timeout=30)


@pytest.mark.asyncio
async def test_openai_compatible_provider_health_check():
    provider = OpenAICompatibleProvider("http://localhost:11434/v1", "llama3.1")
    fake_response = MagicMock(status_code=200)
    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=fake_response)):
        ok, detail = await provider.health()
    assert ok is True
    assert "llama3.1" in detail
