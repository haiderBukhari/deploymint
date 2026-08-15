"""The provider seam: every LLM call in the app goes through this Protocol,
never a specific SDK directly. This is what makes "swap to a local model"
possible without touching Smith, Red Team, Chat, Oracle, or Warden — they
all call core/llm.py's complete()/complete_json(), whose signatures never
change regardless of which provider answers underneath.
See docs/17-pending-work.md §17.6."""

from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """The provider itself was unreachable (network, auth, rate limit) — as
    opposed to a successful call that returned something we couldn't use.
    Callers use this distinction to decide whether a repair attempt makes
    sense (see agents/smith.py) or should go straight to a fallback."""


class Provider(Protocol):
    async def health(self) -> tuple[bool, str]: ...

    async def complete_raw(
        self, system: str, user: str, *, max_tokens: int, temperature: float, timeout: int
    ) -> str: ...
