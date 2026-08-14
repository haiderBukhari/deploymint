import pytest

from deploymint.config import get_settings
from deploymint.core.sandbox import SandboxError, safe_join, validate_repo_path


def test_rejects_outside_workspace(workspace):
    with pytest.raises(SandboxError):
        validate_repo_path("/etc")


def test_rejects_root(workspace):
    with pytest.raises(SandboxError):
        validate_repo_path("/")


def test_rejects_missing(workspace):
    with pytest.raises(SandboxError):
        validate_repo_path(str(workspace / "does-not-exist"))


def test_rejects_traversal(workspace):
    with pytest.raises(SandboxError):
        safe_join(workspace, "../../etc/passwd")


def test_accepts_dir_under_workspace(workspace):
    (workspace / "my-app").mkdir()
    assert validate_repo_path(str(workspace / "my-app")).is_dir()


def test_rejects_empty_path(workspace):
    with pytest.raises(SandboxError):
        validate_repo_path("")
