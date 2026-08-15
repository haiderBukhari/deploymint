"""Application settings. See docs/05-phase-1-foundation.md Step 1.2."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEPLOYMINT_", extra="ignore")

    # server
    host: str = "0.0.0.0"
    port: int = 8000
    sql_echo: bool = False

    # database — DATABASE_URL (no prefix, shared convention) set by docker-compose.yml.
    # Falls back to a local dev default when running outside Compose.
    database_url_env: str = "postgresql+psycopg://deploymint:deploymint@localhost:5432/deploymint"

    # llm — see docs/04-agents-spec.md §4.10 and docs/06-phase-2-generation.md
    # ANTHROPIC_API_KEY is read directly (no DEPLOYMINT_ prefix) — it's the
    # standard convention the Anthropic SDK itself uses, matching DATABASE_URL below.
    model: str = "claude-opus-5"
    llm_timeout: int = 120

    # llm provider seam — see docs/17-pending-work.md §17.6. "anthropic" is the
    # default and only what's been production-tested; "openai_compatible" talks
    # to any local runtime that speaks the OpenAI chat-completions shape
    # (Ollama, vLLM, LM Studio), via llm_base_url, e.g.
    # http://host.docker.internal:11434/v1 for a host-run Ollama.
    llm_provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    llm_base_url: str = ""

    # the sandbox root — see docs/01-architecture.md §1.7. Inside the container this
    # is always /workspace. Tests override it to a tmp directory.
    workspace_root: Path = Path("/workspace")

    # kubernetes — optional; falls back to `docker run` if unreachable
    kube_context: str = ""
    rollout_timeout: int = 120

    # security
    block_severity: str = "critical"  # critical | high | medium
    enable_redteam: bool = True

    # runtime
    max_concurrent_runs: int = 2
    max_repo_files: int = 5000

    # observability — see docs/10-phase-6-finops-ui.md §6.1. Tests shrink these
    # to keep the Oracle's watch window from dominating the test suite's runtime.
    oracle_samples: int = 12
    oracle_interval: int = 5

    @property
    def database_url(self) -> str:
        return os.environ.get("DATABASE_URL", self.database_url_env)

    @property
    def anthropic_api_key(self) -> str:
        return os.environ.get("ANTHROPIC_API_KEY", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
