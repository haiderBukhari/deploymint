"""Architect Agent: deterministic repo analysis, plus one small optional LLM
call for a plain-English summary. See docs/04-agents-spec.md §4.1,
docs/29-richer-reasoning.md, and docs/32-architect-thread-offload.md.

The scan/parse/graph-build chain below is 100% synchronous CPU/disk work
(tree-sitter parsing, networkx algorithms) — run()  offloads it to a worker
thread via asyncio.to_thread, the same pattern core/docker_engine.py's
build_image() already uses for its own blocking call. Without this, since
the app runs as a single uvicorn worker, this chain running directly on the
event loop freezes EVERY concurrently in-flight request — including a bare
GET / with no relation to any run — for the scan's full duration."""

import asyncio
import json
import sys
from pathlib import Path

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.core import llm, prompts, repo_scanner
from deploymint.core.graph_builder import (
    build_import_graph,
    find_cycles,
    rank_criticality,
    to_node_link,
)


def _scan_and_analyze(root: Path) -> dict:
    """The synchronous chain, unchanged in behavior from before — just
    extracted so run() can offload it via asyncio.to_thread. Returns either
    {"analysis": ..., "errors": [...]} or, on a walk failure,
    {"analysis": _empty_analysis(), "errors": [...]}."""
    errors: list[str] = []

    try:
        scan = repo_scanner.walk_repo(root)
    except Exception as e:
        return {"analysis": _empty_analysis(), "errors": [f"architect: walk failed: {e}"],
                "walk_failed": True}

    if scan.truncated:
        errors.append(f"architect: truncated at {repo_scanner.MAX_FILES} files")

    language, package_manager, manifest_path = repo_scanner.detect_language(root, scan.files)
    framework = repo_scanner.detect_framework(language, manifest_path, root)
    entrypoint = repo_scanner.find_entrypoint(root, language, scan.files)
    exposed_port = repo_scanner.infer_port(root, framework, entrypoint)
    dependencies = repo_scanner.detect_dependencies(language, manifest_path, root)
    services = repo_scanner.detect_microservices(root)
    has_tests = any("test" in str(f.relative_to(root)).lower() for f in scan.files)
    dockerfile_exists = (root / "Dockerfile").exists()

    graph = build_import_graph(root, scan.files, language)
    critical_files = rank_criticality(graph)
    cycles = find_cycles(graph)
    if cycles:
        errors.append("architect: circular import detected: " + " -> ".join(cycles[0]))

    analysis = {
        "language": language,
        "framework": framework,
        "package_manager": package_manager,
        "entrypoint": entrypoint,
        "exposed_port": exposed_port,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "file_count": len(scan.files),
        "dependencies": dependencies,
        "services": services,
        "graph": to_node_link(graph),
        "critical_files": critical_files,
        "has_tests": has_tests,
        "dockerfile_exists": dockerfile_exists,
        # Previously computed then discarded into an error string only —
        # now persisted so a diagram/summary can reference it directly.
        "cycles": cycles,
    }
    return {"analysis": analysis, "errors": errors}


class ArchitectAgent(BaseAgent):
    name = "architect"

    async def run(self, state: DeployState) -> dict:
        root = Path(state["repo_path"])

        scanned = await asyncio.to_thread(_scan_and_analyze, root)
        if scanned.get("walk_failed"):
            # Matches the original early-return exactly: no architect.done
            # emit, no summarize call, on a walk failure.
            return {
                "analysis": scanned["analysis"],
                "errors": state.get("errors", []) + scanned["errors"],
            }

        analysis = scanned["analysis"]
        errors = scanned["errors"]
        analysis["architecture_summary"] = await self._summarize(analysis)

        await self.emit(
            "architect.done",
            language=analysis["language"],
            framework=analysis["framework"],
            file_count=analysis["file_count"],
            entrypoint=analysis["entrypoint"],
        )

        result: dict = {"analysis": analysis}
        if errors:
            result["errors"] = state.get("errors", []) + errors
        return result

    async def _summarize(self, analysis: dict) -> str:
        """ARCHITECT_SUMMARY_PROMPT existed but was never called from
        anywhere — the Architect made zero LLM calls, so
        `analysis["architecture_summary"]` (rendered by project.html) was
        permanently empty. Never raises: an empty summary just means no
        caption, not a failed analysis — the deterministic fields above are
        the actual output this agent is responsible for."""
        try:
            compact = {k: v for k, v in analysis.items() if k not in ("graph", "dependencies")}
            summary = await llm.complete(
                system="You summarize a codebase's architecture for a developer "
                       "seeing it for the first time.",
                user=prompts.ARCHITECT_SUMMARY_PROMPT.format(
                    analysis_json=json.dumps(compact, indent=2)),
                max_tokens=200,
            )
            return summary.strip()
        except Exception:
            return ""


def _empty_analysis() -> dict:
    return {
        "language": "unknown", "framework": "unknown", "package_manager": "unknown",
        "entrypoint": "", "exposed_port": 8000, "python_version": "3.11",
        "file_count": 0, "dependencies": [], "services": [], "graph": {"nodes": [], "links": []},
        "critical_files": [], "has_tests": False, "dockerfile_exists": False,
        "cycles": [], "architecture_summary": "",
    }
