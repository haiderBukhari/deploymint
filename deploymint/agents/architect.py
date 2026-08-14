"""Architect Agent: deterministic repo analysis. See docs/04-agents-spec.md §4.1."""

import sys
from pathlib import Path

from deploymint.agents.base import BaseAgent
from deploymint.agents.state import DeployState
from deploymint.core import repo_scanner
from deploymint.core.graph_builder import (
    build_import_graph,
    find_cycles,
    rank_criticality,
    to_node_link,
)


class ArchitectAgent(BaseAgent):
    name = "architect"

    async def run(self, state: DeployState) -> dict:
        repo_path = state["repo_path"]
        root = Path(repo_path)
        errors: list[str] = []

        try:
            scan = repo_scanner.walk_repo(root)
        except Exception as e:
            return {
                "analysis": _empty_analysis(),
                "errors": state.get("errors", []) + [f"architect: walk failed: {e}"],
            }

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
            errors.append(
                "architect: circular import detected: " + " -> ".join(cycles[0])
            )

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
        }

        await self.emit(
            "architect.done",
            language=language,
            framework=framework,
            file_count=len(scan.files),
            entrypoint=entrypoint,
        )

        result: dict = {"analysis": analysis}
        if errors:
            result["errors"] = state.get("errors", []) + errors
        return result


def _empty_analysis() -> dict:
    return {
        "language": "unknown", "framework": "unknown", "package_manager": "unknown",
        "entrypoint": "", "exposed_port": 8000, "python_version": "3.11",
        "file_count": 0, "dependencies": [], "services": [], "graph": {"nodes": [], "links": []},
        "critical_files": [], "has_tests": False, "dockerfile_exists": False,
    }
