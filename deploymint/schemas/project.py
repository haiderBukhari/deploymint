from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_path: str
    cloud_provider: Literal["aws", "gcp", "azure"] = "aws"

    @field_validator("name")
    @classmethod
    def slug_safe(cls, v: str) -> str:
        cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in v.lower())
        if not cleaned.strip("-"):
            raise ValueError("name must contain at least one alphanumeric character")
        return cleaned


class ProjectRead(BaseModel):
    id: int
    name: str
    repo_path: str
    language: str | None = None
    framework: str | None = None
    entrypoint: str | None = None
    exposed_port: int = 8000
    cloud_provider: str = "aws"
    created_at: datetime
    last_analyzed_at: datetime | None = None

    model_config = {"from_attributes": True}
