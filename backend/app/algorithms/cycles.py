"""Circular import detection.

PERFORMANCE WARNING -- read before changing this.

Calling nx.simple_cycles(G) directly on a real repository graph will hang. Its
cost is O((n + e)(c + 1)) in the number of simple cycles c, and c grows
combinatorially in densely tangled dependency graphs.

The approach here stays bounded:

1. Find strongly connected components. This is linear, and an SCC with more
   than one node is already proof that a cycle exists -- enough to report the
   health signal even when enumeration is capped.
2. Enumerate concrete cycles only *inside* each SCC subgraph, with both a
   length bound and a hard count cap.
3. Report `truncated` when a cap was hit, so the caller never mistakes a capped
   result for a complete one.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import networkx as nx


@dataclass(slots=True)
class Cycle:
    paths: list[str]

    @property
    def length(self) -> int:
        return len(self.paths)

    def to_dict(self) -> dict:
        return {"paths": self.paths, "length": self.length}


@dataclass(slots=True)
class CycleReport:
    cycles: list[Cycle] = field(default_factory=list)
    scc_count: int = 0
    files_in_cycles: int = 0
    self_loops: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "cycles": [c.to_dict() for c in self.cycles],
            "scc_count": self.scc_count,
            "files_in_cycles": self.files_in_cycles,
            "self_loops": self.self_loops,
            "truncated": self.truncated,
            "has_cycles": bool(self.cycles or self.self_loops),
        }


def find_cycles(
    file_graph: nx.DiGraph,
    length_bound: int = 8,
    max_cycles_per_scc: int = 50,
) -> CycleReport:
    report = CycleReport()
    if file_graph.number_of_nodes() == 0:
        return report

    def path_of(node: str) -> str:
        return file_graph.nodes[node].get("path", node)

    report.self_loops = sorted(path_of(n) for n in nx.nodes_with_selfloops(file_graph))

    components = [c for c in nx.strongly_connected_components(file_graph) if len(c) > 1]
    report.scc_count = len(components)
    report.files_in_cycles = sum(len(c) for c in components)

    collected: list[Cycle] = []
    for component in components:
        subgraph = file_graph.subgraph(component)
        found = list(
            itertools.islice(
                nx.simple_cycles(subgraph, length_bound=length_bound), max_cycles_per_scc
            )
        )
        if len(found) >= max_cycles_per_scc:
            report.truncated = True
        for cycle in found:
            collected.append(Cycle(paths=[path_of(n) for n in cycle]))

    # Shortest first: a 2-file cycle is far more actionable than an 8-file one.
    collected.sort(key=lambda c: (c.length, c.paths))
    report.cycles = collected
    return report
