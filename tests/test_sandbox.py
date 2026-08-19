import pytest

from deploymint.core.sandbox import SandboxError, list_workspace_dirs, safe_join, validate_repo_path


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


def test_list_workspace_dirs_is_empty_when_nothing_registered(workspace):
    assert list_workspace_dirs() == []


def test_list_workspace_dirs_lists_real_subdirectories(workspace):
    (workspace / "my-app").mkdir()
    (workspace / "another-app").mkdir()
    assert list_workspace_dirs() == ["another-app", "my-app"]


def test_list_workspace_dirs_ignores_files_and_dotdirs(workspace):
    (workspace / "real-app").mkdir()
    (workspace / ".hidden").mkdir()
    (workspace / "not-a-dir.txt").write_text("x")
    assert list_workspace_dirs() == ["real-app"]
