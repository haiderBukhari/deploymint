from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, computed_field

RunStatus = Literal["pending", "running", "success", "failed", "blocked", "cancelled"]

# claude-opus-5 pricing: $5/1M input, $25/1M output. Update if the model changes.
_INPUT_PER_TOKEN = 5e-6
_OUTPUT_PER_TOKEN = 25e-6


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

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def llm_cost_usd(self) -> float | None:
        if self.input_tokens is None:
            return None
        return round(
            self.input_tokens * _INPUT_PER_TOKEN
            + (self.output_tokens or 0) * _OUTPUT_PER_TOKEN, 4,
        )
