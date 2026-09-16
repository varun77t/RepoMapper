"""Build a networkx DiGraph from a RepoAnalysis.

EDGE DIRECTION IS A CONTRACT -- everything downstream depends on it:

    imports         A -> B  means "A imports B"
    defines         file -> symbol
    calls           caller symbol -> callee symbol
    inherits_from   subclass -> base class

Because an import edge points from the importer to the imported, a widely
depended-upon file accumulates *incoming* edges and therefore scores highly
under PageRank with no graph reversal anywhere. Flipping this direction would
silently invert the central-files ranking.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from app.parsing.types import SymbolKind

if TYPE_CHECKING:
    # Type-only import: graphing sits *below* services, so importing it at
    # runtime would invert the layering for the sake of an annotation.
    from app.services.analyzer import RepoAnalysis

NodeType = str
FILE = "file"


def file_node_id(path: str) -> str:
    return f"file:{path}"


def symbol_node_id(path: str, local_id: int) -> str:
    return f"sym:{path}#{local_id}"


def _resolve_base_class(
    analysis: RepoAnalysis, path: str, base_name: str
) -> tuple[str, int] | None:
    """Map a base-class expression to a class symbol, best effort.

    `React.Component` and other external bases simply do not resolve, which is
    the correct outcome -- they are not nodes in this repo's graph.
    """
    simple = base_name.split(".")[-1].split("[")[0].strip()
    if not simple:
        return None

    own = analysis.files.get(path)
    if own is not None:
        for sym in own.symbols:
            if sym.kind is SymbolKind.CLASS and sym.name == simple:
                return (path, sym.local_id)

    matches = [
        (p, s.local_id)
        for p, parsed in analysis.files.items()
        for s in parsed.symbols
        if s.kind is SymbolKind.CLASS and s.name == simple
    ]
    return matches[0] if len(matches) == 1 else None


def build_graph(analysis: RepoAnalysis) -> nx.DiGraph:
    graph = nx.DiGraph()

    for path, parsed in analysis.files.items():
        graph.add_node(
            file_node_id(path),
            type=FILE,
            label=path.rsplit("/", 1)[-1],
            path=path,
            language=parsed.language.value if parsed.language else None,
            line_count=parsed.line_count,
            size_bytes=parsed.size_bytes,
            symbol_count=len(parsed.symbols),
        )

    for path, parsed in analysis.files.items():
        for sym in parsed.symbols:
            node_id = symbol_node_id(path, sym.local_id)
            graph.add_node(
                node_id,
                type=sym.kind.value,
                label=sym.qualified_name,
                name=sym.name,
                path=path,
                start_line=sym.start_line,
                end_line=sym.end_line,
                params=list(sym.params),
                is_exported=sym.is_exported,
                is_nested=sym.is_nested,
                decorators=list(sym.decorators),
                parent_node_id=(
                    symbol_node_id(path, sym.parent_local_id)
                    if sym.parent_local_id is not None
                    else None
                ),
            )
            graph.add_edge(file_node_id(path), node_id, type="defines")

    for imp in analysis.imports:
        if imp.target_path is None:
            continue
        source, target = file_node_id(imp.source_path), file_node_id(imp.target_path)
        if source == target or not graph.has_node(target):
            continue
        if graph.has_edge(source, target):
            graph[source][target]["weight"] += 1
        else:
            graph.add_edge(source, target, type="imports", weight=1)

    for call in analysis.calls:
        if call.callee is None:
            continue
        source = symbol_node_id(*call.caller)
        target = symbol_node_id(*call.callee)
        if not graph.has_node(source) or not graph.has_node(target) or source == target:
            continue
        if graph.has_edge(source, target):
            graph[source][target]["weight"] += 1
        else:
            graph.add_edge(
                source, target, type="calls", weight=1,
                resolution=call.tier.value, confidence=call.confidence,
            )

    for path, parsed in analysis.files.items():
        for sym in parsed.symbols:
            if sym.kind is not SymbolKind.CLASS:
                continue
            for base in sym.base_classes:
                resolved = _resolve_base_class(analysis, path, base)
                if resolved is None:
                    continue
                source = symbol_node_id(path, sym.local_id)
                target = symbol_node_id(*resolved)
                if source != target and graph.has_node(target):
                    graph.add_edge(source, target, type="inherits_from")

    return graph


def file_subgraph(graph: nx.DiGraph) -> nx.DiGraph:
    """File nodes plus import edges only.

    This is the graph every Phase 3 algorithm operates on, except orphan
    detection, which needs the symbol level too.
    """
    sub = nx.DiGraph()
    for node, data in graph.nodes(data=True):
        if data.get("type") == FILE:
            sub.add_node(node, **data)
    for source, target, data in graph.edges(data=True):
        if data.get("type") == "imports" and sub.has_node(source) and sub.has_node(target):
            sub.add_edge(source, target, **data)
    return sub
