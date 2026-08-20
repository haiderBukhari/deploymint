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
    # default and only what's been production-tested.
    # "openai" talks to the real OpenAI API (gpt-4o etc.) using OPENAI_API_KEY.
    # "openai_compatible" talks to any LOCAL runtime that speaks the same
    # chat-completions shape (Ollama, vLLM, LM Studio) via llm_base_url, e.g.
    # http://host.docker.internal:11434/v1 for a host-run Ollama — same code
    # path as "openai", just pointed elsewhere with no key required.
    llm_provider: Literal["anthropic", "openai", "openai_compatible"] = "anthropic"
    llm_base_url: str = ""

    # the sandbox root — see docs/01-architecture.md §1.7. Inside the container this
    # is always /workspace. Tests override it to a tmp directory.
    workspace_root: Path = Path("/workspace")

    # kubernetes — optional; falls back to `docker run` if unreachable
    kube_context: str = ""
    rollout_timeout: int = 120
    # Auto-provision a real local `kind` cluster (via kube_engine.ensure_kind_cluster)
    # when deploy_mode="kubernetes" and no cluster is reachable, instead of
    # silently falling back to `docker run`. Default OFF: today's near-instant
    # docker-run fallback must not silently become a 30-90s+ cluster-creation
    # step for everyone who never asked for it — and it's effectively
    # Linux-only today (Docker Desktop on macOS/Windows runs the daemon in a
    # VM, so the created cluster's kubeconfig server address isn't guaranteed
    # reachable from inside this container). See docs/35-kind-cluster.md.
    enable_auto_kind_cluster: bool = False

    # security
    block_severity: str = "critical"  # critical | high | medium
    enable_redteam: bool = True
    # An LLM-driven checklist audit of the user's actual source code (secrets,
    # missing auth, injection risk, etc.) — replaces a dependency-CVE scanner
    # (Trivy, then Grype) that was tried and dropped: too slow to bootstrap
    # (a large vulnerability DB had to download before the first scan could
    # run at all) and too noisy (200+ mostly base-OS-package findings nobody
    # could act on). See docs/34-code-audit.md.
    enable_code_audit: bool = True
    # Pause after the Architect node with status="awaiting_approval", showing
    # the user the architecture diagram + a plan with editable knobs before
    # generation proceeds. See docs/33-deploy-lock-and-findings.md.
    enable_approval_gate: bool = True

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

    @property
    def openai_api_key(self) -> str:
        return os.environ.get("OPENAI_API_KEY", "")

    @property
    def secret_key(self) -> str:
        """Fernet key encrypting stored cloud credentials (docs/36-monitoring.md)
        — deliberately env-var-only, matching anthropic_api_key/openai_api_key
        above, so the key never lives next to the ciphertext it protects."""
        return os.environ.get("DEPLOYMINT_SECRET_KEY", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
