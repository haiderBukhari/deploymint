"""Security-critical path sandboxing. See docs/05-phase-1-foundation.md Step 1.3."""

from pathlib import Path

from deploymint.config import get_settings


class SandboxError(ValueError):
    pass


def validate_repo_path(raw: str) -> Path:
    """Resolve and validate a user-supplied path. Must live under workspace_root —
    there is no other directory the app has any legitimate reason to touch."""
    if not raw or not raw.strip():
        raise SandboxError("repo_path is empty")

    root = get_settings().workspace_root.resolve()
    p = Path(raw).expanduser()
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise SandboxError(f"path does not exist or cannot be resolved: {raw}") from e

    if not p.is_dir():
        raise SandboxError(f"not a directory: {p}")
    if not p.is_relative_to(root):
        raise SandboxError(f"path must be under {root}: {p}")
    return p


def safe_join(root: Path, relative: str) -> Path:
    """Join and assert the result stays inside root."""
    candidate = (root / relative).resolve()
    root = root.resolve()
    if not candidate.is_relative_to(root):
        raise SandboxError(f"path traversal blocked: {relative}")
    return candidate
