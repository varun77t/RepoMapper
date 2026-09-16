"""Which files is the rest of the codebase most dependent on?

Pure functions over a networkx graph: no database, no HTTP, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx


@dataclass(slots=True)
class CentralFile:
    rank: int
    node_id: str
    path: str
    pagerank: float
    in_degree: int
    out_degree: int

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "node_id": self.node_id,
            "path": self.path,
            "pagerank": round(self.pagerank, 6),
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
        }


def compute_pagerank(
    file_graph: nx.DiGraph, alpha: float = 0.85, condense_cycles: bool = True
) -> dict[str, float]:
    """PageRank over the import graph.

    Import edges point importer -> imported, so rank flows *to* depended-upon
    files and no reversal is needed.

    Cycles are condensed first, and this is not a detail -- it fixes a wrong
    answer. A circular-import group is a PageRank spider trap: rank flows into
    the cycle and then circulates between its members forever with no way out,
    while a genuinely central leaf module (imported by many, importing nothing)
    is a dangling node whose rank is redistributed away. On a fixture where
    config.py has in-degree 4 and a 2-file import cycle has in-degree 2, raw
    PageRank ranks the cycle 1st and 2nd and config.py only 3rd.

    Collapsing each strongly connected component into a single node removes the
    trap by construction, and is defensible on its own terms: a circular group
    is one unit of dependency, so it earns one score.

    Every member of a component inherits that score in full -- it is NOT divided
    by the component size. Dividing penalises membership of a cycle, which gets
    the real-world case badly wrong: Flask's core modules form one 22-file SCC,
    and dividing ranked flask/typing.py (in-degree 1) above flask/__init__.py
    (in-degree 52). Within a component there is by definition no ordering, so
    rank_centrality breaks the resulting ties on in-degree.

    Pass condense_cycles=False for the raw textbook score.
    """
    if file_graph.number_of_nodes() == 0:
        return {}
    if not condense_cycles:
        return nx.pagerank(file_graph, alpha=alpha)

    condensed = nx.condensation(file_graph)
    component_scores = nx.pagerank(condensed, alpha=alpha)
    return {
        node: component_scores[component]
        for node, component in condensed.graph["mapping"].items()
    }


def rank_centrality(
    file_graph: nx.DiGraph, top_n: int = 15, pagerank: dict[str, float] | None = None
) -> list[CentralFile]:
    scores = compute_pagerank(file_graph) if pagerank is None else pagerank
    if not scores:
        return []

    # in_degree breaks ties deterministically; without it, equally ranked files
    # would come back in arbitrary (hash) order and tests would flake.
    ordered = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], -file_graph.in_degree(kv[0]), kv[0]),
    )
    return [
        CentralFile(
            rank=i + 1,
            node_id=node,
            path=file_graph.nodes[node].get("path", node),
            pagerank=score,
            in_degree=file_graph.in_degree(node),
            out_degree=file_graph.out_degree(node),
        )
        for i, (node, score) in enumerate(ordered[:top_n])
    ]
