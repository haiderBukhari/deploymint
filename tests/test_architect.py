import threading
from unittest.mock import patch

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


@pytest.mark.asyncio
async def test_cycles_are_persisted_on_the_analysis(sample_repo):
    """Previously cycles were computed then discarded into an error string
    only — never persisted, so project.html's cycle-warning block and any
    future diagram had nothing to read. See docs/29-richer-reasoning.md."""
    with patch(
        "deploymint.agents.architect.find_cycles",
        return_value=[["app/a.py", "app/b.py", "app/a.py"]],
    ):
        result = await ArchitectAgent().run({"repo_path": str(sample_repo), "errors": []})
    a = result["analysis"]
    assert a["cycles"] == [["app/a.py", "app/b.py", "app/a.py"]]
    assert any("circular import" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_no_cycles_means_empty_list_and_no_error(sample_repo):
    result = await ArchitectAgent().run({"repo_path": str(sample_repo), "errors": []})
    assert result["analysis"]["cycles"] == []
    assert "errors" not in result or not any(
        "circular import" in e for e in result["errors"]
    )


@pytest.mark.asyncio
async def test_architecture_summary_populated_from_llm(sample_repo):
    with patch("deploymint.core.llm.complete", return_value="  A tidy FastAPI service.  "):
        result = await ArchitectAgent().run({"repo_path": str(sample_repo), "errors": []})
    assert result["analysis"]["architecture_summary"] == "A tidy FastAPI service."


@pytest.mark.asyncio
async def test_architecture_summary_degrades_to_empty_string_on_llm_failure(sample_repo):
    """The Architect must never raise because its LLM call failed — the
    deterministic analysis fields are the actual contract, the summary is a
    bonus caption."""
    with patch("deploymint.core.llm.complete", side_effect=RuntimeError("boom")):
        result = await ArchitectAgent().run({"repo_path": str(sample_repo), "errors": []})
    assert result["analysis"]["architecture_summary"] == ""
    assert result["analysis"]["language"] == "python"


@pytest.mark.asyncio
async def test_scan_runs_off_the_event_loop_thread(sample_repo):
    """Regression test: the whole synchronous scan/parse/graph-build chain
    used to run directly on the event loop — since the app is a single
    uvicorn worker, this froze every concurrently in-flight request
    (including a bare GET /) for the scan's full duration. It must now run
    via asyncio.to_thread, i.e. NOT on the main thread. See
    docs/32-architect-thread-offload.md."""
    from deploymint.agents import architect as architect_module

    seen_threads = []
    real_scan = architect_module._scan_and_analyze

    def spy(root):
        seen_threads.append(threading.current_thread())
        return real_scan(root)

    with patch("deploymint.agents.architect._scan_and_analyze", side_effect=spy):
        await ArchitectAgent().run({"repo_path": str(sample_repo), "errors": []})

    assert seen_threads, "the scan function was never called"
    assert seen_threads[0] is not threading.main_thread(), (
        "the scan ran on the main/event-loop thread — asyncio.to_thread "
        "offload is missing or was bypassed"
    )


@pytest.mark.asyncio
async def test_empty_repo_has_empty_cycles_and_summary(workspace):
    empty = workspace / "empty2"
    empty.mkdir()
    result = await ArchitectAgent().run({"repo_path": str(empty), "errors": []})
    a = result["analysis"]
    assert a["cycles"] == []
    assert a["architecture_summary"] == ""
