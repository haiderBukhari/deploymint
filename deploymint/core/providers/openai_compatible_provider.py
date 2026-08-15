"""For local runtimes that speak the OpenAI chat-completions shape — Ollama,
vLLM, and LM Studio all do, so this one adapter covers "swap to a local
LLaMA/DeepSeek" without a dependency per backend or pulling in a full proxy
layer like LiteLLM. Uses httpx directly (already a dependency) rather than
adding the `openai` SDK for a single endpoint shape.
See docs/17-pending-work.md §17.6."""

import httpx

from deploymint.core.providers.base import LLMError, LLMUnavailable


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key: str = ""):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    async def health(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{self._base_url}/chat/completions",
                    json={"model": self._model, "max_tokens": 8,
                          "messages": [{"role": "user", "content": "ok"}]},
                    headers=self._headers(),
                )
            if r.status_code == 200:
                return True, f"{self._model} reachable at {self._base_url}"
            return False, f"{self._base_url} returned {r.status_code}: {r.text[:150]}"
        except httpx.ConnectError as e:
            return False, f"cannot reach {self._base_url}: {e}"
        except Exception as e:
            return False, str(e)[:200]

    async def complete_raw(
        self, system: str, user: str, *, max_tokens: int, temperature: float, timeout: int
    ) -> str:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{self._base_url}/chat/completions",
                    json={
                        "model": self._model,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                        "max_tokens": max_tokens, "temperature": temperature,
                    },
                    headers=self._headers(),
                )
        except httpx.ConnectError as e:
            raise LLMUnavailable(f"cannot reach {self._base_url}: {e}") from e
        except httpx.TimeoutException as e:
            raise LLMError(f"timed out after {timeout}s") from e

        if r.status_code == 429:
            raise LLMUnavailable(f"rate limited: {r.text[:150]}")
        if r.status_code >= 400:
            raise LLMError(f"api error {r.status_code}: {r.text[:200]}")

        data = r.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            raise LLMError("model declined the request")
        return choice["message"]["content"]
