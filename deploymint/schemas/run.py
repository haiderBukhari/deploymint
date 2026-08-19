from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, computed_field

RunStatus = Literal["pending", "running", "success", "failed", "blocked", "cancelled"]

# Per-1M-token pricing (input, output) in USD, keyed by model prefix — a run's
# actual model_used decides which row applies, so switching providers (e.g.
# DEPLOYMINT_LLM_PROVIDER=openai) doesn't silently keep pricing a completely
# different model's tokens. Falls back to claude-opus-5's rate for anything
# unrecognized (a local/self-hosted model, or a new model not added here yet)
# rather than guessing — see docs/17-pending-work.md §17.6.
_PRICING_PER_1M = {
    "claude-opus-5": (5.0, 25.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-5": (5.0, 15.0),
}
_DEFAULT_PRICING = _PRICING_PER_1M["claude-opus-5"]


def _pricing_for(model: str | None) -> tuple[float, float]:
    if not model:
        return _DEFAULT_PRICING
    for prefix, rates in _PRICING_PER_1M.items():
        if model.startswith(prefix):
            return rates
    return _DEFAULT_PRICING


class RunCreate(BaseModel):
    force: bool = False
    trigger: str = "api"
    skip_deploy: bool = False


class RunRead(BaseModel):
    id: str
    project_id: int
    status: RunStatus
    current_node: str | None = None
    analysis: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    security: dict[str, Any] | None = None
    deployment: dict[str, Any] | None = None
    cost: dict[str, Any] | None = None
    errors: list[str] = []
    model_used: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
    created_at: datetime
    completed_at: datetime | None = None
    cloud_deploy_status: str | None = None
    cloud_deploy_action: str | None = None
    cloud_deploy_output: str | None = None
    cloud_deployed_at: datetime | None = None

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def llm_cost_usd(self) -> float | None:
        if self.input_tokens is None:
            return None
        input_rate, output_rate = _pricing_for(self.model_used)
        return round(
            self.input_tokens * (input_rate / 1e6)
            + (self.output_tokens or 0) * (output_rate / 1e6), 4,
        )
