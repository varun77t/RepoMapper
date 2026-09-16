"""Convert graphs to JSON for storage and for the API.

These are deliberately two different functions.

nx.node_link_data is used only for *persistence*, and always with an explicit
edges kwarg: its default changed from "links" to "edges" (NetworkX 3.4 emits a
FutureWarning and 3.6 completes the change). Letting that default leak into the
HTTP response would silently rename a field the frontend depends on during a
routine dependency bump.

The API shape is therefore hand-written and owned by us, not by networkx.
"""
from __future__ import annotations

import networkx as nx

from app.graphing.builder import FILE

# Pin the kwarg rather than inherit a shifting default.
_EDGES_KEY = "edges"

_SYMBOL_TYPES = frozenset({"function", "method", "class"})


def graph_to_storage(graph: nx.DiGraph) -> dict:
    return nx.node_link_data(graph, edges=_EDGES_KEY)


def graph_from_storage(data: dict) -> nx.DiGraph:
    return nx.node_link_graph(data, directed=True, edges=_EDGES_KEY)


def graph_to_api(
    graph: nx.DiGraph,
    level: str = "file",
    pagerank: dict[str, float] | None = None,
    communities: list | None = None,
) -> dict:
    """Flat {nodes, edges} shape.

    Kept library-neutral: Cytoscape.js wraps each entry in {data: {...}} at the
    frontend boundary with a single map, so the contract does not bind us to a
    visualisation library.
    """
    community_of: dict[str, int] = {}
    for community in communities or []:
        for node_id in community.get("node_ids", []):
            community_of[node_id] = community.get("id", 0)

    if level == "file":
        keep = {n for n, d in graph.nodes(data=True) if d.get("type") == FILE}
        edge_types = {"imports"}
    elif level == "symbol":
        keep = {n for n, d in graph.nodes(data=True) if d.get("type") in _SYMBOL_TYPES}
        edge_types = {"calls", "inherits_from"}
    else:  # "all"
        keep = set(graph.nodes)
        edge_types = {"imports", "calls", "inherits_from", "defines"}

    # Degrees must be counted over the edges this view actually returns.
    # Taking them from the full graph would report config.py as out_degree 4
    # when it imports nothing -- those edges are `defines` to its own symbols.
    in_degree: dict[str, int] = {n: 0 for n in keep}
    out_degree: dict[str, int] = {n: 0 for n in keep}
    kept_edges = [
        (s, t, d)
        for s, t, d in graph.edges(data=True)
        if d.get("type") in edge_types and s in keep and t in keep
    ]
    for source, target, _ in kept_edges:
        out_degree[source] += 1
        in_degree[target] += 1

    nodes = []
    for node_id in sorted(keep):
        data = graph.nodes[node_id]
        entry = {
            "id": node_id,
            "label": data.get("label", node_id),
            "type": data.get("type"),
            "path": data.get("path"),
        }
        if data.get("type") == FILE:
            entry.update(
                language=data.get("language"),
                line_count=data.get("line_count"),
                symbol_count=data.get("symbol_count"),
                pagerank=round(pagerank.get(node_id, 0.0), 6) if pagerank else None,
                community=community_of.get(node_id),
                in_degree=in_degree[node_id],
                out_degree=out_degree[node_id],
            )
        else:
            entry.update(
                name=data.get("name"),
                start_line=data.get("start_line"),
                end_line=data.get("end_line"),
                is_exported=data.get("is_exported"),
                parent_node_id=data.get("parent_node_id"),
            )
        nodes.append(entry)

    edges = []
    for source, target, data in kept_edges:
        edge_type = data.get("type")
        edge = {
            "id": f"{source}->{target}:{edge_type}",
            "source": source,
            "target": target,
            "type": edge_type,
            "weight": data.get("weight", 1),
        }
        if edge_type == "calls":
            edge["resolution"] = data.get("resolution")
            edge["confidence"] = data.get("confidence")
        edges.append(edge)
    edges.sort(key=lambda e: e["id"])

    return {"level": level, "nodes": nodes, "edges": edges}
