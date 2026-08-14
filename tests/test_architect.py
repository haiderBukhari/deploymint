import pytest

from deploymint.agents.architect import ArchitectAgent


@pytest.mark.asyncio
async def test_detects_fastapi_and_ranks_db_most_critical(sample_repo):
    result = await ArchitectAgent().run({"repo_path": str(sample_repo), "errors": []})
    a = result["analysis"]

    assert a["language"] == "python"
    assert a["framework"] == "fastapi"
    assert a["entrypoint"] == "main.py"
    assert a["exposed_port"] == 8000
    assert a["file_count"] >= 4
    assert len(a["graph"]["nodes"]) >= 4
    assert len(a["graph"]["links"]) >= 3
    # db.py is imported by both models.py and routes.py — the most-imported
    # module must rank first. This is the exact PageRank-direction bug caught
    # during implementation (see docs/04-agents-spec.md §4.1).
    assert a["critical_files"][0] == "app/db.py"


@pytest.mark.asyncio
async def test_empty_repo_degrades_gracefully(workspace):
    empty = workspace / "empty"
    empty.mkdir()
    result = await ArchitectAgent().run({"repo_path": str(empty), "errors": []})
    a = result["analysis"]
    assert a["language"] == "unknown"
    assert a["critical_files"] == []


@pytest.mark.asyncio
async def test_binary_file_does_not_crash_scan(workspace):
    repo = workspace / "bin-repo"
    repo.mkdir()
    (repo / "main.py").write_text("import os\n")
    (repo / "blob.bin").write_bytes(bytes(range(256)) * 10)
    result = await ArchitectAgent().run({"repo_path": str(repo), "errors": []})
    assert result["analysis"]["language"] == "python"
