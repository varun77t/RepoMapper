"""Import and call resolution -- the linchpin every graph result depends on."""
from __future__ import annotations

from app.parsing.resolution.calls import Tier
from app.parsing.resolution.js_imports import JsModuleIndex
from app.parsing.resolution.python_imports import PythonModuleIndex


class TestPythonImportResolution:
    def test_every_fixture_import_resolves(self, py_analysis):
        unresolved = [
            i for i in py_analysis.imports if i.target_path is None and not i.is_external
        ]
        assert unresolved == []

    def test_exact_import_edge_set(self, py_analysis):
        assert sorted(py_analysis.import_edges) == [
            ("app.py", "cycle_a.py"),
            ("app.py", "db.py"),
            ("app.py", "models.py"),
            ("cycle_a.py", "cycle_b.py"),
            ("cycle_b.py", "cycle_a.py"),
            ("db.py", "config.py"),
            ("main.py", "app.py"),
            ("main.py", "utils.py"),
            ("models.py", "config.py"),
            ("orphan.py", "config.py"),
            ("utils.py", "config.py"),
        ]

    def test_package_init_resolves_to_init_file(self):
        index = PythonModuleIndex({"pkg/__init__.py", "pkg/mod.py", "main.py"})
        assert index.resolve("main.py", "pkg", 0, []).targets == ["pkg/__init__.py"]

    def test_submodule_of_package(self):
        index = PythonModuleIndex({"pkg/__init__.py", "pkg/mod.py", "main.py"})
        assert index.resolve("main.py", "pkg.mod", 0, []).targets == ["pkg/mod.py"]

    def test_from_package_import_submodule(self):
        """`from pkg import mod` must reach pkg/mod.py, not only pkg/__init__.py."""
        index = PythonModuleIndex({"pkg/__init__.py", "pkg/mod.py", "main.py"})
        targets = index.resolve("main.py", "pkg", 0, ["mod"]).targets
        assert set(targets) == {"pkg/__init__.py", "pkg/mod.py"}

    def test_relative_import_single_dot(self):
        index = PythonModuleIndex({"pkg/__init__.py", "pkg/a.py", "pkg/b.py"})
        assert index.resolve("pkg/a.py", "b", 1, []).targets == ["pkg/b.py"]

    def test_relative_import_from_dot_import_name(self):
        """`from . import b` depends on both the package and the submodule.

        Importing from the package executes pkg/__init__.py, and `b` loads
        pkg/b.py, so both are genuine dependencies of the importing file.
        """
        index = PythonModuleIndex({"pkg/__init__.py", "pkg/a.py", "pkg/b.py"})
        targets = index.resolve("pkg/a.py", "", 1, ["b"]).targets
        assert set(targets) == {"pkg/__init__.py", "pkg/b.py"}

    def test_relative_import_parent_package(self):
        index = PythonModuleIndex(
            {"pkg/__init__.py", "pkg/sub/__init__.py", "pkg/sub/a.py", "pkg/other.py"}
        )
        assert index.resolve("pkg/sub/a.py", "other", 2, []).targets == ["pkg/other.py"]

    def test_src_layout_is_a_root(self):
        index = PythonModuleIndex({"src/pkg/__init__.py", "src/pkg/mod.py", "src/main.py"})
        assert index.resolve("src/main.py", "pkg.mod", 0, []).targets == ["src/pkg/mod.py"]

    def test_package_parent_is_a_root(self):
        """`backend/app/...` is the common shape of a deployed web app.

        Nothing in that tree carries packaging metadata, so before the parent
        of a top-level package counted as a root, every `import app.x` fell
        through to "external" and the repo produced a graph with no edges.
        """
        index = PythonModuleIndex(
            {
                "backend/app/__init__.py",
                "backend/app/config.py",
                "backend/app/routers/__init__.py",
                "backend/app/routers/chat.py",
                "backend/requirements.txt",
            }
        )
        assert index.resolve("backend/app/main.py", "app.config", 0, []).targets == [
            "backend/app/config.py"
        ]
        assert index.resolve(
            "backend/app/main.py", "app.routers.chat", 0, []
        ).targets == ["backend/app/routers/chat.py"]

    def test_nested_package_does_not_become_a_root(self):
        """Only the top of a package chain puts its parent on the path."""
        index = PythonModuleIndex(
            {"backend/app/__init__.py", "backend/app/sub/__init__.py", "backend/app/sub/a.py"}
        )
        # `sub.a` would only resolve if backend/app were treated as a root.
        assert index.resolve("backend/app/main.py", "sub.a", 0, []).is_external is True
        assert index.resolve("backend/app/main.py", "app.sub.a", 0, []).targets == [
            "backend/app/sub/a.py"
        ]

    def test_stdlib_is_external(self):
        index = PythonModuleIndex({"main.py"})
        outcome = index.resolve("main.py", "os.path", 0, [])
        assert outcome.is_external is True
        assert outcome.external_module == "os"
        assert outcome.targets == []

    def test_self_import_produces_no_edge(self):
        index = PythonModuleIndex({"a.py"})
        assert index.resolve("a.py", "a", 0, []).targets == []


class TestJsImportResolution:
    def test_exact_import_edge_set(self, ts_analysis):
        assert sorted(ts_analysis.import_edges) == [
            ("src/app.ts", "src/components/Widget.tsx"),
            ("src/app.ts", "src/lib/format.ts"),
            ("src/app.ts", "src/lib/index.ts"),
            ("src/components/Widget.tsx", "src/lib/format.ts"),
            ("src/index.ts", "src/app.ts"),
            ("src/index.ts", "src/lib/format.ts"),
            ("src/lib/index.ts", "src/lib/format.ts"),
            ("src/orphan.ts", "src/lib/format.ts"),
        ]

    def test_directory_specifier_resolves_to_index_file(self, ts_analysis):
        """`import { describe } from "./lib"` -> src/lib/index.ts"""
        assert ("src/app.ts", "src/lib/index.ts") in ts_analysis.import_edges

    def test_tsconfig_star_alias(self, ts_analysis):
        """`@/lib/format` via paths {"@/*": ["*"]} with baseUrl src."""
        assert ("src/app.ts", "src/lib/format.ts") in ts_analysis.import_edges

    def test_tsconfig_prefixed_alias(self, ts_analysis):
        """`~lib/format` via paths {"~lib/*": ["lib/*"]}."""
        assert ("src/index.ts", "src/lib/format.ts") in ts_analysis.import_edges

    def test_bare_specifier_is_external(self, ts_analysis):
        react = [i for i in ts_analysis.imports if i.raw_specifier == "react"]
        assert react and all(i.is_external and i.external_module == "react" for i in react)

    def test_scoped_package_keeps_scope(self):
        index = JsModuleIndex({"src/a.ts"}, {})
        assert index.resolve("src/a.ts", "@scope/pkg").external_module == "@scope/pkg"

    def test_extension_probe_prefers_ts_over_js(self):
        index = JsModuleIndex({"a.ts", "a.js", "main.ts"}, {})
        assert index.resolve("main.ts", "./a").targets == ["a.ts"]

    def test_parent_relative_specifier(self):
        index = JsModuleIndex({"src/lib/f.ts", "src/components/W.tsx"}, {})
        assert index.resolve("src/components/W.tsx", "../lib/f").targets == ["src/lib/f.ts"]

    def test_jsonc_tsconfig_with_comments_and_trailing_commas(self):
        config = """{
          // a comment
          "compilerOptions": {
            /* block */
            "baseUrl": "src",
            "paths": { "@/*": ["*"], },
          },
        }"""
        index = JsModuleIndex({"src/lib/f.ts", "src/main.ts"}, {"tsconfig.json": config})
        assert index.resolve("src/main.ts", "@/lib/f").targets == ["src/lib/f.ts"]


class TestReferencedTsconfig:
    """Vite's scaffold splits tsconfig across three files.

    tsconfig.json holds only `references`; the path aliases live in
    tsconfig.app.json. Reading just the canonical filename finds an empty
    compilerOptions and every `@/...` import resolves to "external".
    """

    ROOT = '{"files": [], "references": [{"path": "./tsconfig.app.json"}]}'
    APP = """{
      "compilerOptions": {
        "baseUrl": ".",
        "paths": { "@/*": ["./src/*"] }
      }
    }"""

    def test_alias_from_a_referenced_config(self):
        index = JsModuleIndex(
            {"frontend/src/App.tsx", "frontend/src/hooks/useAuth.tsx"},
            {
                "frontend/tsconfig.json": self.ROOT,
                "frontend/tsconfig.app.json": self.APP,
            },
        )
        assert index.resolve("frontend/src/App.tsx", "@/hooks/useAuth").targets == [
            "frontend/src/hooks/useAuth.tsx"
        ]

    def test_alias_resolves_against_its_own_config_directory(self):
        """Two frontends, same alias, different targets."""
        configs = {
            "web/tsconfig.json": '{"compilerOptions":{"baseUrl":".","paths":{"~/*":["./src/*"]}}}',
            "admin/tsconfig.json": '{"compilerOptions":{"baseUrl":".","paths":{"#/*":["./src/*"]}}}',
        }
        index = JsModuleIndex({"web/src/a.ts", "admin/src/b.ts"}, configs)
        assert index.resolve("web/src/main.ts", "~/a").targets == ["web/src/a.ts"]
        assert index.resolve("admin/src/main.ts", "#/b").targets == ["admin/src/b.ts"]


class TestCallResolution:
    def test_no_in_repo_call_is_left_unresolved(self, py_analysis, ts_analysis):
        for analysis in (py_analysis, ts_analysis):
            assert analysis.call_stats["unresolved_count"] == 0
            assert analysis.call_stats["resolved_pct"] == 100.0

    def test_builtins_are_not_counted_as_failures(self, py_analysis):
        """print/str/strip are not repo symbols; counting them as unresolved
        would understate accuracy."""
        assert py_analysis.call_stats["builtin_count"] > 0
        builtin_names = {c.callee_name for c in py_analysis.calls if c.tier is Tier.BUILTIN}
        assert {"print", "str"} <= builtin_names

    def test_local_tier_same_file_function(self, py_analysis):
        call = next(
            c for c in py_analysis.calls
            if c.caller[0] == "utils.py" and c.callee_name == "slugify"
        )
        assert call.tier is Tier.LOCAL
        assert call.callee[0] == "utils.py"
        assert call.confidence == 1.0

    def test_local_tier_self_method(self, py_analysis):
        """self.parse() resolves to the method on the enclosing class."""
        call = next(
            c for c in py_analysis.calls
            if c.caller[0] == "db.py" and c.callee_name == "parse"
        )
        assert call.tier is Tier.LOCAL
        assert call.callee[0] == "db.py"

    def test_import_tier_module_qualified(self, py_analysis):
        """db.connect() -- receiver is an imported module name."""
        call = next(
            c for c in py_analysis.calls
            if c.caller[0] == "app.py" and c.callee_name == "connect"
        )
        assert call.tier is Tier.IMPORT
        assert call.callee[0] == "db.py"

    def test_import_tier_from_import(self, py_analysis):
        """get_setting() after `from config import get_setting`."""
        call = next(
            c for c in py_analysis.calls
            if c.caller[0] == "utils.py" and c.callee_name == "get_setting"
        )
        assert call.tier is Tier.IMPORT
        assert call.callee[0] == "config.py"

    def test_heuristic_tier_unique_repo_wide_name(self, py_analysis):
        """user.save() -- receiver type is unknown, but only one `save` exists."""
        call = next(
            c for c in py_analysis.calls
            if c.caller[0] == "models.py" and c.callee_name == "save"
        )
        assert call.tier is Tier.HEURISTIC
        assert call.confidence == 0.5
        assert call.callee[0] == "models.py"

    def test_js_import_tier(self, ts_analysis):
        call = next(
            c for c in ts_analysis.calls
            if c.caller[0] == "src/index.ts" and c.callee_name == "createApp"
        )
        assert call.tier is Tier.IMPORT
        assert call.callee[0] == "src/app.ts"

    def test_stats_shape(self, py_analysis):
        stats = py_analysis.call_stats
        assert stats["total_calls"] == stats["in_repo_calls"] + stats["builtin_count"]
        assert stats["resolved_count"] == (
            stats["local_count"] + stats["import_count"] + stats["heuristic_count"]
        )


class TestCommonJsImportResolution:
    def test_parent_traversal_to_repo_root(self):
        """`require("../..")` from examples/auth/ means the root index.js."""
        index = JsModuleIndex({"index.js", "examples/auth/index.js"}, {})
        assert index.resolve("examples/auth/index.js", "../..").targets == ["index.js"]

    def test_trailing_slash_parent_traversal(self):
        index = JsModuleIndex({"index.js", "examples/a/index.js"}, {})
        assert index.resolve("examples/a/index.js", "../../").targets == ["index.js"]

    def test_require_alias_enables_cross_file_call_resolution(self):
        from app.parsing.js_extractor import extract as js_extract
        from app.parsing.resolution.calls import CallResolver, Tier
        from app.parsing.types import Language

        caller = js_extract(
            "app.js",
            b"var utils = require('./utils');\n"
            b"exports.run = function () { return utils.merge(1); };\n",
            Language.JAVASCRIPT,
        )
        target = js_extract(
            "utils.js", b"exports.merge = function (a) { return a; };\n", Language.JAVASCRIPT
        )
        files = {"app.js": caller, "utils.js": target}
        import_targets = {
            "app.js": [(caller.imports[0], ["utils.js"])],
            "utils.js": [],
        }
        calls = CallResolver(files, import_targets).resolve_all()
        merge = next(c for c in calls if c.callee_name == "merge")
        assert merge.tier is Tier.IMPORT
        assert merge.callee[0] == "utils.js"
