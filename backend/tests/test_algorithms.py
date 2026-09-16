"""The five graph algorithms, asserted against a fixture with known answers.

The fixture import graph (A -> B means A imports B):

    main.py  -> app.py, utils.py
    app.py   -> db.py, models.py, cycle_a.py
    db.py    -> config.py
    models.py-> config.py
    utils.py -> config.py
    orphan.py-> config.py          (nothing imports orphan.py)
    cycle_a.py <-> cycle_b.py      (deliberate circular import)

So config.py is the most depended-upon file (in-degree 4), main.py is the
entry point, cycle_a/cycle_b are the only cycle, and orphan.py is the only
unreferenced file.
"""
from __future__ import annotations

import networkx as nx
import pytest

from app.algorithms.centrality import compute_pagerank, rank_centrality
from app.algorithms.communities import detect_communities
from app.algorithms.cycles import find_cycles
from app.algorithms.orphans import find_orphans
from app.algorithms.reading_order import detect_entry_points, suggest_reading_order


def _paths(graph, nodes):
    return [graph.nodes[n].get("path", n) for n in nodes]


class TestCentrality:
    def test_most_depended_upon_file_ranks_first(self, py_files):
        ranked = rank_centrality(py_files)
        assert ranked[0].path == "config.py"
        assert ranked[0].in_degree == 4
        assert ranked[0].out_degree == 0

    def test_ts_most_depended_upon_file_ranks_first(self, ts_files):
        ranked = rank_centrality(ts_files)
        assert ranked[0].path == "src/lib/format.ts"
        assert ranked[0].in_degree == 5

    def test_condensing_cycles_prevents_the_spider_trap(self, py_files):
        """A 2-file import cycle must not outrank a file imported 4 times.

        Raw PageRank traps rank inside the cycle (it circulates forever with no
        exit) while config.py, a dangling node, leaks its rank away. This is the
        regression guard for that bug.
        """
        raw = compute_pagerank(py_files, condense_cycles=False)
        condensed = compute_pagerank(py_files, condense_cycles=True)

        top_raw = max(raw, key=lambda n: raw[n])
        top_condensed = max(condensed, key=lambda n: condensed[n])

        assert py_files.nodes[top_raw]["path"].startswith("cycle_")
        assert py_files.nodes[top_condensed]["path"] == "config.py"

    def test_ranks_are_dense_and_ordered(self, py_files):
        ranked = rank_centrality(py_files)
        assert [c.rank for c in ranked] == list(range(1, len(ranked) + 1))
        scores = [c.pagerank for c in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_top_n_is_respected(self, py_files):
        assert len(rank_centrality(py_files, top_n=3)) == 3

    def test_empty_graph_returns_empty(self):
        assert rank_centrality(nx.DiGraph()) == []
        assert compute_pagerank(nx.DiGraph()) == {}


class TestCommunities:
    def test_cycle_members_cluster_together(self, py_files):
        communities = detect_communities(py_files)
        clusters = [set(c.paths) for c in communities]
        assert any({"cycle_a.py", "cycle_b.py"} <= cluster for cluster in clusters)

    def test_partition_covers_every_file_exactly_once(self, py_files):
        communities = detect_communities(py_files)
        seen = [p for c in communities for p in c.paths]
        assert sorted(seen) == sorted(
            py_files.nodes[n]["path"] for n in py_files.nodes
        )
        assert len(seen) == len(set(seen))

    def test_is_deterministic_across_runs(self, py_files):
        """Louvain is randomised; without a fixed seed this would flake."""
        first = [sorted(c.paths) for c in detect_communities(py_files, seed=42)]
        second = [sorted(c.paths) for c in detect_communities(py_files, seed=42)]
        assert first == second

    def test_ids_are_assigned_largest_first(self, py_files):
        communities = detect_communities(py_files)
        assert [c.id for c in communities] == list(range(len(communities)))
        sizes = [c.size for c in communities]
        assert sizes == sorted(sizes, reverse=True)

    def test_ts_communities_track_directory_structure(self, ts_files):
        communities = detect_communities(ts_files)
        lib = next(c for c in communities if "src/lib/format.ts" in c.paths)
        assert lib.common_directory == "src/lib"
        assert "format.ts" in lib.label

    def test_empty_graph_returns_empty(self):
        assert detect_communities(nx.DiGraph()) == []


class TestCycles:
    def test_finds_exactly_the_planted_cycle(self, py_files):
        report = find_cycles(py_files)
        assert len(report.cycles) == 1
        assert set(report.cycles[0].paths) == {"cycle_a.py", "cycle_b.py"}
        assert report.cycles[0].length == 2

    def test_reports_scc_summary(self, py_files):
        report = find_cycles(py_files)
        assert report.scc_count == 1
        assert report.files_in_cycles == 2
        assert report.truncated is False
        assert report.to_dict()["has_cycles"] is True

    def test_acyclic_graph_reports_none(self, ts_files):
        report = find_cycles(ts_files)
        assert report.cycles == []
        assert report.scc_count == 0
        assert report.to_dict()["has_cycles"] is False

    def test_self_loop_is_detected(self):
        graph = nx.DiGraph()
        graph.add_node("file:a.py", path="a.py", type="file")
        graph.add_edge("file:a.py", "file:a.py", type="imports")
        assert find_cycles(graph).self_loops == ["a.py"]

    def test_length_bound_excludes_longer_cycles(self):
        """A 5-file cycle is invisible under length_bound=3."""
        graph = nx.DiGraph()
        names = [f"f{i}.py" for i in range(5)]
        for name in names:
            graph.add_node(f"file:{name}", path=name, type="file")
        for i in range(5):
            graph.add_edge(f"file:{names[i]}", f"file:{names[(i + 1) % 5]}", type="imports")

        assert find_cycles(graph, length_bound=3).cycles == []
        # The SCC summary still reports the tangle even when enumeration finds
        # nothing -- that is the point of computing SCCs separately.
        assert find_cycles(graph, length_bound=3).scc_count == 1
        assert len(find_cycles(graph, length_bound=8).cycles) == 1

    def test_truncation_is_flagged(self):
        """A fully connected group has many cycles; the cap must be reported."""
        graph = nx.DiGraph()
        names = [f"f{i}.py" for i in range(6)]
        for name in names:
            graph.add_node(f"file:{name}", path=name, type="file")
        for a in names:
            for b in names:
                if a != b:
                    graph.add_edge(f"file:{a}", f"file:{b}", type="imports")

        report = find_cycles(graph, length_bound=6, max_cycles_per_scc=5)
        assert len(report.cycles) == 5
        assert report.truncated is True

    def test_empty_graph(self):
        assert find_cycles(nx.DiGraph()).cycles == []


class TestReadingOrder:
    def test_entry_point_is_detected_by_convention(self, py_files):
        entries = _paths(py_files, detect_entry_points(py_files))
        assert "main.py" in entries
        # orphan.py has in-degree 0 but is not a conventional entry point;
        # if it were treated as one, nothing could ever be flagged an orphan.
        assert "orphan.py" not in entries

    def test_order_starts_at_the_entry_point(self, py_files):
        steps = suggest_reading_order(py_files)
        assert steps[0].path == "main.py"
        assert steps[0].reason == "entry point"

    def test_every_file_appears_exactly_once(self, py_files):
        steps = suggest_reading_order(py_files)
        paths = [s.path for s in steps]
        assert len(paths) == py_files.number_of_nodes()
        assert len(set(paths)) == len(paths)

    def test_importer_is_read_before_what_it_imports(self, py_files):
        """Within the reachable path, a file never precedes its importer.

        Unreachable files are exempt by design: they are appended at the end, so
        an orphan that imports config.py is listed after it. Burying the main
        reading path behind unreferenced modules would be worse.
        """
        steps = suggest_reading_order(py_files)
        position = {s.path: s.step for s in steps}
        in_cycle = {s.path for s in steps if s.in_cycle}
        unreachable = {s.path for s in steps if s.depth == -1}

        for source, target in py_files.edges:
            src = py_files.nodes[source]["path"]
            dst = py_files.nodes[target]["path"]
            if (src in in_cycle and dst in in_cycle) or src in unreachable:
                continue
            assert position[src] < position[dst], f"{src} should precede {dst}"

    def test_bfs_depth_is_not_the_primary_sort_key(self):
        """Regression: depth-first ordering inverts edges.

        With main -> y, main -> p -> q -> x and x -> y, y sits at BFS depth 1
        and x at depth 3. Sorting by depth would emit y before x even though x
        imports y, so topological index must take precedence.
        """
        graph = nx.DiGraph()
        for name in ("main.py", "y.py", "p.py", "q.py", "x.py"):
            graph.add_node(f"file:{name}", path=name, type="file")
        for src, dst in [
            ("main.py", "y.py"), ("main.py", "p.py"), ("p.py", "q.py"),
            ("q.py", "x.py"), ("x.py", "y.py"),
        ]:
            graph.add_edge(f"file:{src}", f"file:{dst}", type="imports")

        position = {s.path: s.step for s in suggest_reading_order(graph)}
        assert position["x.py"] < position["y.py"]

    def test_cycle_members_are_flagged_not_dropped(self, py_files):
        steps = suggest_reading_order(py_files)
        flagged = {s.path for s in steps if s.in_cycle}
        assert flagged == {"cycle_a.py", "cycle_b.py"}

    def test_unreachable_files_come_last(self, py_files):
        steps = suggest_reading_order(py_files)
        assert steps[-1].path == "orphan.py"
        assert steps[-1].depth == -1

    def test_steps_are_numbered_from_one(self, py_files):
        steps = suggest_reading_order(py_files)
        assert [s.step for s in steps] == list(range(1, len(steps) + 1))

    def test_falls_back_to_in_degree_when_no_convention_matches(self):
        graph = nx.DiGraph()
        for name in ("alpha.py", "beta.py"):
            graph.add_node(f"file:{name}", path=name, type="file")
        graph.add_edge("file:alpha.py", "file:beta.py", type="imports")
        assert _paths(graph, detect_entry_points(graph)) == ["alpha.py"]

    def test_topological_sort_survives_a_fully_cyclic_graph(self):
        """Condensation guarantees the sort cannot fail, however tangled."""
        graph = nx.DiGraph()
        names = [f"f{i}.py" for i in range(4)]
        for name in names:
            graph.add_node(f"file:{name}", path=name, type="file")
        for i in range(4):
            graph.add_edge(f"file:{names[i]}", f"file:{names[(i + 1) % 4]}", type="imports")

        steps = suggest_reading_order(graph)
        assert len(steps) == 4
        assert all(s.in_cycle for s in steps)


class TestOrphans:
    def test_finds_exactly_the_unreferenced_file(self, py_graph, py_files):
        report = find_orphans(py_graph, py_files)
        assert [f["path"] for f in report.files] == ["orphan.py"]

    def test_entry_point_is_not_an_orphan(self, py_graph, py_files):
        report = find_orphans(py_graph, py_files)
        assert "main.py" not in [f["path"] for f in report.files]

    def test_unused_private_function_is_high_confidence(self, py_graph, py_files):
        report = find_orphans(py_graph, py_files)
        helper = next(s for s in report.symbols if s["label"] == "_private_helper")
        assert helper["confidence"] == "high"
        assert helper["path"] == "utils.py"

    def test_symbols_inside_an_orphan_file_are_not_repeated(self, py_graph, py_files):
        report = find_orphans(py_graph, py_files)
        assert all(s["path"] != "orphan.py" for s in report.symbols)

    def test_entry_point_symbols_are_excluded(self, py_graph, py_files):
        """main() is invoked by the interpreter, not by anything importable."""
        report = find_orphans(py_graph, py_files)
        assert all(s["label"] != "main" for s in report.symbols)

    def test_exported_symbols_are_low_confidence(self, ts_graph, ts_files):
        """An unused export may be public API, so it cannot be called dead."""
        report = find_orphans(ts_graph, ts_files)
        widget = next(s for s in report.symbols if s["label"] == "Widget")
        assert widget["confidence"] == "low"

    def test_method_of_exported_class_is_low_confidence(self, ts_graph, ts_files):
        """React calls render(); the class escaping the repo makes it reachable."""
        report = find_orphans(ts_graph, ts_files)
        render = next(s for s in report.symbols if s["label"] == "Widget.render")
        assert render["confidence"] == "low"

    def test_decorated_functions_are_never_orphans(self, py_graph, py_files):
        graph = nx.DiGraph()
        graph.add_node("file:v.py", path="v.py", type="file")
        graph.add_node(
            "sym:v.py#0", type="function", path="v.py", name="handler",
            label="handler", decorators=["app.route"], is_exported=True,
        )
        files = nx.DiGraph()
        files.add_node("file:v.py", path="v.py", type="file")
        assert find_orphans(graph, files).symbols == []

    def test_dunder_methods_are_never_orphans(self, py_graph, py_files):
        report = find_orphans(py_graph, py_files)
        assert all("__init__" not in s["label"] for s in report.symbols)

    @pytest.mark.parametrize(
        "path", ["tests/test_x.py", "src/__init__.py", "conftest.py", "a/x.test.ts"]
    )
    def test_excluded_paths_are_never_orphans(self, path):
        graph = nx.DiGraph()
        graph.add_node(f"file:{path}", path=path, type="file")
        files = nx.DiGraph()
        files.add_node(f"file:{path}", path=path, type="file")
        assert find_orphans(graph, files).files == []


class TestInsightsOrchestration:
    def test_all_sections_present(self, py_insights):
        assert set(py_insights) == {
            "central_files", "communities", "cycles", "reading_order",
            "orphans", "entry_points", "summary",
        }

    def test_summary_counts_match_sections(self, py_insights):
        summary = py_insights["summary"]
        assert summary["file_count"] == 9
        assert summary["import_edge_count"] == 11
        assert summary["community_count"] == len(py_insights["communities"])
        assert summary["cycle_count"] == len(py_insights["cycles"]["cycles"])
        assert summary["orphan_file_count"] == len(py_insights["orphans"]["files"])

    def test_output_is_json_serialisable(self, py_insights, ts_insights):
        import json

        assert json.loads(json.dumps(py_insights))
        assert json.loads(json.dumps(ts_insights))
