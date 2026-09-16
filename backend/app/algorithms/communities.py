"""Auto-cluster files into subsystems with the Louvain algorithm.

The point is that these clusters are derived from *actual import coupling*, not
from the folder layout -- so they can reveal that a "utils" directory is really
three unrelated subsystems, or that two packages are one tangled module.
"""
from __future__ import annotations

import posixpath
from collections import Counter
from dataclasses import dataclass, field

import networkx as nx

from app.algorithms.centrality import compute_pagerank

# Fixed seed: Louvain is randomised, and without this the partition (and every
# test asserting on it) changes between runs.
DEFAULT_SEED = 42


@dataclass(slots=True)
class Community:
    id: int
    label: str
    node_ids: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    central_paths: list[str] = field(default_factory=list)
    common_directory: str = ""

    @property
    def size(self) -> int:
        return len(self.node_ids)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "size": self.size,
            "node_ids": self.node_ids,
            "paths": self.paths,
            "central_paths": self.central_paths,
            "common_directory": self.common_directory,
        }


def _to_weighted_undirected(file_graph: nx.DiGraph) -> nx.Graph:
    """Louvain requires an undirected graph; collapse A->B and B->A into weight."""
    undirected = nx.Graph()
    undirected.add_nodes_from(file_graph.nodes(data=True))
    for source, target, data in file_graph.edges(data=True):
        weight = data.get("weight", 1)
        if undirected.has_edge(source, target):
            undirected[source][target]["weight"] += weight
        else:
            undirected.add_edge(source, target, weight=weight)
    return undirected


def _common_directory(paths: list[str]) -> str:
    dirs = [posixpath.dirname(p) for p in paths if posixpath.dirname(p)]
    if not dirs:
        return ""
    return Counter(dirs).most_common(1)[0][0]


def detect_communities(
    file_graph: nx.DiGraph,
    pagerank: dict[str, float] | None = None,
    seed: int = DEFAULT_SEED,
) -> list[Community]:
    if file_graph.number_of_nodes() == 0:
        return []

    scores = compute_pagerank(file_graph) if pagerank is None else pagerank
    partitions = nx.community.louvain_communities(
        _to_weighted_undirected(file_graph), seed=seed, weight="weight"
    )

    communities: list[Community] = []
    for members in partitions:
        ranked = sorted(
            members, key=lambda n: (-scores.get(n, 0.0), n)
        )
        paths = [file_graph.nodes[n].get("path", n) for n in ranked]
        central = paths[: min(2, len(paths))]
        directory = _common_directory(paths)
        # Name the cluster after its most depended-upon member -- that is the
        # file someone would recognise the subsystem by.
        label = posixpath.basename(central[0]) if central else "unknown"
        if directory:
            label = f"{directory}/ ({label})"
        communities.append(
            Community(
                id=0,
                label=label,
                node_ids=ranked,
                paths=paths,
                central_paths=central,
                common_directory=directory,
            )
        )

    # Largest first, then by label, so ids are stable across runs.
    communities.sort(key=lambda c: (-c.size, c.label))
    for index, community in enumerate(communities):
        community.id = index
    return communities
