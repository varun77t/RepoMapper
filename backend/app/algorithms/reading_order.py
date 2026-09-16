"""Suggested onboarding reading order.

Produces a linear "read this file, then this one" path starting from the repo's
entry points and following imports outward.

Cycles are handled by construction rather than by special-casing: the graph is
condensed (nx.condensation collapses every strongly connected component into a
single node), which is guaranteed acyclic, so the topological sort cannot fail
no matter how tangled the imports are. Files inside a cycle are still emitted,
flagged with in_cycle=True, ordered among themselves by PageRank.

Ordering rules, in order of precedence:

1. Files reachable from an entry point come before files that are not. An
   onboarding path should not open with a module nothing references.
2. Within each group, order is the topological order of the condensation, so an
   importer is always read before what it imports.

BFS depth from the entry points is reported but deliberately NOT used as the
primary sort key. Sorting by depth first can invert an edge: given
main -> y, main -> p -> q -> x and x -> y, y sits at depth 1 and x at depth 3,
so depth-first ordering would place y before x even though x imports it.

The one place rule 1 outranks rule 2 is an unreachable file importing a
reachable one (an orphan module importing config). The orphan is still listed
last, because burying the main reading path behind unreferenced files would be
the worse outcome.
"""
from __future__ import annotations

import posixpath
from collections import deque
from dataclasses import dataclass

import networkx as nx

from app.algorithms.centrality import compute_pagerank

# Unambiguous entry points: these names mean "run me" at any depth.
STRONG_ENTRY_POINT_NAMES = frozenset({
    "__main__.py", "main.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "index.jsx", "index.tsx",
    "main.js", "main.ts", "main.jsx", "main.tsx",
})

# Suggestive but ambiguous: app.py is an entry point at the repo root and an
# ordinary library module at src/flask/app.py. Treating the latter as an entry
# point pollutes the reading order of every library, so these only count near
# the top of the tree.
WEAK_ENTRY_POINT_NAMES = frozenset({
    "app.py", "cli.py", "server.py", "run.py",
    "app.js", "app.ts", "app.jsx", "app.tsx", "server.js", "server.ts",
})

# Repo root or one directory down.
WEAK_ENTRY_POINT_MAX_DEPTH = 1

ENTRY_POINT_NAMES = STRONG_ENTRY_POINT_NAMES | WEAK_ENTRY_POINT_NAMES


@dataclass(slots=True)
class ReadingStep:
    step: int
    node_id: str
    path: str
    reason: str
    depth: int
    in_cycle: bool = False

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "node_id": self.node_id,
            "path": self.path,
            "reason": self.reason,
            "depth": self.depth,
            "in_cycle": self.in_cycle,
        }


def detect_entry_points(file_graph: nx.DiGraph) -> list[str]:
    """Entry points, by filename convention first.

    The in-degree-0 fallback only applies when no conventional entry point
    exists. If it were applied unconditionally, every unimported file would be
    an "entry point" and orphan detection could never flag anything.
    """
    by_name = []
    for node in file_graph.nodes:
        path = file_graph.nodes[node].get("path", node)
        basename = posixpath.basename(path)
        if basename in STRONG_ENTRY_POINT_NAMES:
            by_name.append(node)
        elif (
            basename in WEAK_ENTRY_POINT_NAMES
            and path.count("/") <= WEAK_ENTRY_POINT_MAX_DEPTH
        ):
            by_name.append(node)
    if by_name:
        return sorted(by_name)
    return sorted(n for n in file_graph.nodes if file_graph.in_degree(n) == 0)


def suggest_reading_order(
    file_graph: nx.DiGraph,
    pagerank: dict[str, float] | None = None,
) -> list[ReadingStep]:
    if file_graph.number_of_nodes() == 0:
        return []

    scores = compute_pagerank(file_graph) if pagerank is None else pagerank
    entry_points = set(detect_entry_points(file_graph))

    condensed = nx.condensation(file_graph)
    mapping: dict[str, int] = condensed.graph["mapping"]
    component_members: dict[int, list[str]] = {}
    for node, component in mapping.items():
        component_members.setdefault(component, []).append(node)

    topo_index = {c: i for i, c in enumerate(nx.topological_sort(condensed))}

    # BFS from entry-point components records how far each file sits from an
    # entry point, which is what makes the ordering feel like a reading path
    # rather than an arbitrary valid topological sort.
    entry_components = {mapping[n] for n in entry_points}
    depth: dict[int, int] = {c: 0 for c in entry_components}
    queue = deque(sorted(entry_components))
    while queue:
        component = queue.popleft()
        for successor in condensed.successors(component):
            if successor not in depth:
                depth[successor] = depth[component] + 1
                queue.append(successor)

    # Topological index only -- see the module docstring for why depth must not
    # be the primary key.
    reachable = [c for c in condensed.nodes if c in depth]
    unreachable = [c for c in condensed.nodes if c not in depth]
    ordered_components = sorted(reachable, key=lambda c: topo_index[c]) + sorted(
        unreachable, key=lambda c: topo_index[c]
    )

    steps: list[ReadingStep] = []
    for component in ordered_components:
        nodes = component_members[component]
        in_cycle = len(nodes) > 1
        # Inside a cycle there is no correct order, so lead with the file the
        # rest of the cycle leans on most.
        for node in sorted(nodes, key=lambda n: (-scores.get(n, 0.0), n)):
            if node in entry_points:
                reason = "entry point"
            elif in_cycle:
                reason = "part of a circular dependency group"
            elif component in depth:
                reason = f"imported by earlier files (depth {depth[component]})"
            else:
                reason = "not reachable from any entry point"
            steps.append(
                ReadingStep(
                    step=len(steps) + 1,
                    node_id=node,
                    path=file_graph.nodes[node].get("path", node),
                    reason=reason,
                    depth=depth.get(component, -1),
                    in_cycle=in_cycle,
                )
            )
    return steps
