"""Run every graph algorithm and combine the results.

PageRank is computed once here and threaded through the algorithms that need
it (centrality, communities, reading order) rather than being recomputed three
times -- it is the most expensive step on a large graph.
"""
from __future__ import annotations

import networkx as nx

from app.algorithms.centrality import compute_pagerank, rank_centrality
from app.algorithms.communities import detect_communities
from app.algorithms.cycles import find_cycles
from app.algorithms.orphans import find_orphans
from app.algorithms.reading_order import detect_entry_points, suggest_reading_order
from app.graphing.builder import file_subgraph


def compute_insights(
    graph: nx.DiGraph,
    top_n: int = 15,
    cycle_length_bound: int = 8,
    max_cycles_per_scc: int = 50,
) -> dict:
    files = file_subgraph(graph)
    pagerank = compute_pagerank(files)

    central = rank_centrality(files, top_n=top_n, pagerank=pagerank)
    communities = detect_communities(files, pagerank=pagerank)
    cycle_report = find_cycles(
        files, length_bound=cycle_length_bound, max_cycles_per_scc=max_cycles_per_scc
    )
    reading = suggest_reading_order(files, pagerank=pagerank)
    orphan_report = find_orphans(graph, files)
    entry_points = detect_entry_points(files)

    return {
        "central_files": [c.to_dict() for c in central],
        "communities": [c.to_dict() for c in communities],
        "cycles": cycle_report.to_dict(),
        "reading_order": [s.to_dict() for s in reading],
        "orphans": orphan_report.to_dict(),
        "entry_points": [
            {"node_id": n, "path": files.nodes[n].get("path", n)} for n in entry_points
        ],
        "summary": {
            "file_count": files.number_of_nodes(),
            "import_edge_count": files.number_of_edges(),
            "community_count": len(communities),
            "cycle_count": len(cycle_report.cycles),
            "orphan_file_count": len(orphan_report.files),
            "orphan_symbol_count": len(orphan_report.symbols),
        },
    }
