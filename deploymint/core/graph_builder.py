"""Dependency graph construction + PageRank criticality. See
docs/04-agents-spec.md §4.1 Steps 6-7."""

from pathlib import Path

import networkx as nx

from deploymint.core.repo_scanner import (
    extract_js_imports,
    extract_python_imports,
    resolve_module,
)


def build_import_graph(root: Path, files: list[Path], language: str) -> nx.DiGraph:
    """A -> B edge means "A imports B". Only internal imports become edges;
    external deps are dropped here (they're already captured separately)."""
    graph = nx.DiGraph()

    if language == "python":
        py_files = [f for f in files if f.suffix == ".py"]
        for f in py_files:
            rel = str(f.relative_to(root))
            graph.add_node(rel)
        for f in py_files:
            rel = str(f.relative_to(root))
            try:
                source = f.read_bytes()
            except OSError:
                continue
            try:
                imports = extract_python_imports(source)
            except Exception:
                continue
            for mod, _is_relative in imports:
                target = resolve_module(mod, f, root)
                if target and target != rel:
                    graph.add_edge(rel, target)

    elif language == "javascript":
        js_files = [f for f in files if f.suffix in (".js", ".jsx", ".ts", ".tsx")]
        for f in js_files:
            rel = str(f.relative_to(root))
            graph.add_node(rel)
        for f in js_files:
            rel = str(f.relative_to(root))
            try:
                source = f.read_bytes()
            except OSError:
                continue
            try:
                specifiers = extract_js_imports(source)
            except Exception:
                continue
            for spec in specifiers:
                if not spec.startswith("."):
                    continue  # external package
                target = _resolve_js_relative(spec, f, root, js_files)
                if target and target != rel:
                    graph.add_edge(rel, target)
    else:
        for f in files:
            graph.add_node(str(f.relative_to(root)))

    return graph


def _resolve_js_relative(spec: str, current_file: Path, root: Path, js_files: list[Path]) -> str | None:
    base = (current_file.parent / spec).resolve()
    for suffix in ("", ".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts"):
        candidate = Path(str(base) + suffix)
        if candidate.exists() and candidate in js_files:
            try:
                return str(candidate.relative_to(root))
            except ValueError:
                return None
    return None


def rank_criticality(graph: nx.DiGraph, top_n: int = 5) -> list[str]:
    """Edges point importer -> imported (A imports B => edge A->B), so a node's
    IN-degree already counts how many files depend on it. PageRank computed
    directly on this graph (no reversal) rewards nodes with many incoming
    edges — i.e. heavily-imported, critical modules. Reversing would instead
    rank entrypoints (which import everything but are imported by nothing) as
    most critical, which is backwards. Verified empirically: for
    models->db, routes->db, routes->models, main->routes, the un-reversed
    graph correctly ranks db.py highest (0.42 vs main.py's 0.13)."""
    if graph.number_of_nodes() == 0:
        return []
    try:
        pr = nx.pagerank(graph)
    except Exception:
        return []
    ranked = sorted(pr.items(), key=lambda kv: -kv[1])
    return [n for n, _ in ranked[:top_n]]


def find_cycles(graph: nx.DiGraph, limit: int = 5) -> list[list[str]]:
    try:
        return list(nx.simple_cycles(graph))[:limit]
    except Exception:
        return []


def to_node_link(graph: nx.DiGraph) -> dict:
    return nx.node_link_data(graph, edges="links")
