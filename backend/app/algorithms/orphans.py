"""Dead-code candidates: files and symbols nothing appears to reference.

This is the noisiest of the five algorithms, so the exclusions matter more than
the detection. Without them the output is dominated by false positives:

* package __init__.py files exist to be imported implicitly
* tests are entry points, invoked by a runner and never imported
* config / settings / conftest are loaded by frameworks, not by imports
* a decorated function (@app.route, @pytest.fixture, @task) is called by the
  framework -- a decorator is strong evidence of external invocation
* dunder methods are called by the language itself
* a function nested inside another function is a closure or callback -- it
  escapes by being returned or passed, never by being called by its own name
  (the `wrapper` inside every decorator is the canonical case)
* symbols in an entry-point file are invoked by the runtime, not by an import
  -- main() under `if __name__ == "__main__"` is the canonical example

Exported symbols are still reported but marked low confidence: an unused export
may be dead code, or may be deliberate public API that only callers outside the
repo use. Static analysis cannot tell those apart, so the caller decides.

KNOWN FALSE POSITIVE: a symbol passed by reference rather than called --
a click callback, an event handler, a function stored in a registry dict --
looks identical to dead code here, because only call edges are tracked, not
bare identifier references. Combined with dynamic imports and getattr()
lookups, that is why these are reported as candidates rather than findings.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

import networkx as nx

from app.algorithms.reading_order import detect_entry_points

_TEST_PATTERN = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)(test_[^/]*|[^/]*_test|[^/]*\.(test|spec))\.")
_FRAMEWORK_FILES = frozenset({
    "__init__.py", "conftest.py", "setup.py", "settings.py", "config.py",
    "urls.py", "wsgi.py", "asgi.py", "admin.py", "apps.py", "models.py",
})


@dataclass(slots=True)
class OrphanReport:
    files: list[dict] = field(default_factory=list)
    symbols: list[dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "files": self.files,
            "symbols": self.symbols,
            # A real repo yields many low-confidence (exported) candidates, so
            # give clients the split up front rather than making them count.
            "counts": {
                "files": len(self.files),
                "symbols": len(self.symbols),
                "symbols_high_confidence": sum(
                    1 for s in self.symbols if s.get("confidence") == "high"
                ),
                "symbols_low_confidence": sum(
                    1 for s in self.symbols if s.get("confidence") == "low"
                ),
            },
            "note": self.note,
        }


def _is_excluded_file(path: str) -> bool:
    basename = posixpath.basename(path)
    return basename in _FRAMEWORK_FILES or bool(_TEST_PATTERN.search(path))


def find_orphans(graph: nx.DiGraph, file_graph: nx.DiGraph) -> OrphanReport:
    report = OrphanReport(
        note=(
            "Heuristic. Dynamic imports, reflection and framework auto-discovery "
            "are invisible to static analysis, so treat these as candidates."
        )
    )
    if file_graph.number_of_nodes() == 0:
        return report

    entry_points = set(detect_entry_points(file_graph))

    for node in sorted(file_graph.nodes):
        path = file_graph.nodes[node].get("path", node)
        if node in entry_points or file_graph.in_degree(node) > 0 or _is_excluded_file(path):
            continue
        report.files.append(
            {"node_id": node, "path": path, "reason": "no file in the repo imports it"}
        )

    orphan_files = {entry["path"] for entry in report.files}
    entry_paths = {file_graph.nodes[n].get("path", n) for n in entry_points}
    for node, data in sorted(graph.nodes(data=True)):
        if data.get("type") not in ("function", "method", "class"):
            continue
        path = data.get("path", "")
        name = data.get("name", "")
        if _is_excluded_file(path) or path in orphan_files:
            # Do not report every symbol in an already-reported orphan file.
            continue
        if path in entry_paths:
            # main() is called by the interpreter, not by anything we can see.
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        if data.get("decorators"):
            continue
        if data.get("is_nested"):
            continue
        incoming = [
            source
            for source, _, edge in graph.in_edges(node, data=True)
            if edge.get("type") in ("calls", "inherits_from")
        ]
        if incoming:
            continue
        # A method on an exported class escapes the repo with its class --
        # React lifecycle methods are the common case.
        parent_id = data.get("parent_node_id")
        parent_exported = bool(
            parent_id and graph.nodes.get(parent_id, {}).get("is_exported")
        )
        exported = bool(data.get("is_exported")) or parent_exported
        report.symbols.append(
            {
                "node_id": node,
                "path": path,
                "label": data.get("label", name),
                "kind": data.get("type"),
                "start_line": data.get("start_line"),
                "confidence": "low" if exported else "high",
                "reason": (
                    "reachable from outside the repo (exported) but never called "
                    "inside it; may be public API"
                    if exported
                    else "never called or subclassed anywhere in the repo"
                ),
            }
        )
    return report
