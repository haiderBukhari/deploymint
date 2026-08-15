"""Today's default provider — moved from core/llm.py as-is when the provider
seam was introduced. See docs/17-pending-work.md §17.6."""

import asyncio

import anthropic

from deploymint.core.providers.base import LLMError, LLMUnavailable


class AnthropicProvider:
    def __init__(self, api_key: str, model: str):
        self._model = model
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None

    async def health(self) -> tuple[bool, str]:
        if self._client is None:
            return False, "ANTHROPIC_API_KEY is not set"
        try:
            await asyncio.to_thread(
                self._client.messages.create,
                model=self._model, max_tokens=8,
                messages=[{"role": "user", "content": "ok"}],
            )
            return True, f"{self._model} reachable"
        except anthropic.AuthenticationError:
            return False, "ANTHROPIC_API_KEY is set but invalid"
        except anthropic.APIConnectionError as e:
            return False, f"cannot reach the Anthropic API: {e}"
        except Exception as e:
            return False, str(e)[:200]

    async def complete_raw(
        self, system: str, user: str, *, max_tokens: int, temperature: float, timeout: int
    ) -> str:
        if self._client is None:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.messages.create,
                    model=self._model, max_tokens=max_tokens, temperature=temperature,
                    system=system, messages=[{"role": "user", "content": user}],
                ),
                timeout=timeout,
            )
        except anthropic.RateLimitError as e:
            raise LLMUnavailable(f"rate limited: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMUnavailable(f"cannot reach the API: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"api error {e.status_code}: {e.message}") from e
        except TimeoutError as e:
            raise LLMError(f"timed out after {timeout}s") from e

        if response.stop_reason == "refusal":
            raise LLMError("model declined the request")
        return response.content[0].text
