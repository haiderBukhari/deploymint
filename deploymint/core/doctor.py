"""Prerequisite checks, exposed at GET /api/doctor. See
docs/05-phase-1-foundation.md Step 1.9."""

import asyncio
import shutil

from sqlalchemy import text

from deploymint.config import get_settings


async def run_checks() -> list[dict]:
    checks = []
    checks.append(await _check_database())
    checks.append(_check_workspace())
    checks.append(await _check_llm())
    checks.append(_check_docker_socket())
    checks.append(_check_kubeconfig())
    checks.append(_check_binary("tmux", warn_only=True))
    checks.append(_check_binary("checkov", warn_only=True))
    checks.append(_check_binary("opa", warn_only=True))
    checks.append(_check_cost_source())
    return checks


async def _check_database() -> dict:
    from deploymint.db.database import get_engine

    try:
        engine = get_engine()
        await asyncio.to_thread(_ping_db, engine)
        return {"name": "database", "status": "pass", "detail": "Postgres reachable", "fix": ""}
    except Exception as e:
        return {
            "name": "database", "status": "fail",
            "detail": f"cannot reach Postgres: {str(e)[:200]}",
            "fix": "check DATABASE_URL / docker compose ps db",
        }


def _ping_db(engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _check_workspace() -> dict:
    root = get_settings().workspace_root
    if root.is_dir():
        return {"name": "workspace", "status": "pass", "detail": str(root), "fix": ""}
    return {
        "name": "workspace", "status": "fail",
        "detail": f"{root} does not exist or is not a directory",
        "fix": "check DEPLOYMINT_PROJECTS_DIR / the volume mount",
    }


async def _check_llm() -> dict:
    from deploymint.core import llm

    ok, detail = await llm.health()
    provider = get_settings().llm_provider
    fix = {
        "anthropic": "set ANTHROPIC_API_KEY",
        "openai": "set OPENAI_API_KEY",
        "openai_compatible": "check DEPLOYMINT_LLM_BASE_URL and that the local runtime is up",
    }[provider]
    return {
        "name": "llm", "status": "pass" if ok else "warn", "detail": detail,
        "fix": "" if ok else f"{fix} — templates still work without it",
    }


def _check_docker_socket() -> dict:
    import os

    if os.path.exists("/var/run/docker.sock"):
        return {"name": "docker_socket", "status": "pass", "detail": "mounted", "fix": ""}
    return {
        "name": "docker_socket", "status": "fail", "detail": "not mounted",
        "fix": "mount /var/run/docker.sock in docker-compose.yml",
    }


def _check_kubeconfig() -> dict:
    import os

    path = os.path.expanduser("~/.kube/config")
    if os.path.exists(path):
        return {"name": "kubeconfig", "status": "pass", "detail": path, "fix": ""}
    return {
        "name": "kubeconfig", "status": "warn",
        "detail": "not mounted — deploys will fall back to `docker run`",
        "fix": "mount ~/.kube/config if you have a cluster",
    }


def _check_cost_source() -> dict:
    from deploymint.core import aws_cost

    if not aws_cost.credentials_present():
        return {
            "name": "cost_source", "status": "warn", "detail": "sample data (no AWS credentials)",
            "fix": "set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY for live Cost Explorer data",
        }
    try:
        import boto3  # noqa: F401
    except ImportError:
        return {
            "name": "cost_source", "status": "warn",
            "detail": "sample data (AWS credentials set, but boto3 not installed)",
            "fix": "pip install 'deploymint[aws]'",
        }
    return {"name": "cost_source", "status": "pass", "detail": "live AWS Cost Explorer", "fix": ""}


def _check_binary(name: str, warn_only: bool = False) -> dict:
    path = shutil.which(name)
    if path:
        return {"name": name, "status": "pass", "detail": path, "fix": ""}
    return {
        "name": name,
        "status": "warn" if warn_only else "fail",
        "detail": "not found on PATH",
        "fix": f"install {name}",
    }
