"""Extraction accuracy: exact symbols, imports and call sites."""
from __future__ import annotations

import pytest

from app.parsing.js_extractor import extract as js_extract
from app.parsing.python_extractor import extract as py_extract
from app.parsing.types import Language, SymbolKind


def _names(parsed, kind=None):
    return sorted(s.name for s in parsed.symbols if kind is None or s.kind is kind)


class TestPythonExtraction:
    def test_functions_classes_and_methods(self, py_analysis):
        parsed = py_analysis.files["db.py"]
        assert _names(parsed, SymbolKind.CLASS) == ["Connection"]
        assert _names(parsed, SymbolKind.FUNCTION) == ["connect"]
        assert _names(parsed, SymbolKind.METHOD) == ["__init__", "execute", "parse"]

    def test_methods_are_linked_to_their_class(self, py_analysis):
        parsed = py_analysis.files["db.py"]
        klass = next(s for s in parsed.symbols if s.name == "Connection")
        methods = [s for s in parsed.symbols if s.kind is SymbolKind.METHOD]
        assert all(m.parent_local_id == klass.local_id for m in methods)
        assert {m.qualified_name for m in methods} == {
            "Connection.__init__", "Connection.execute", "Connection.parse"
        }

    def test_base_classes(self, py_analysis):
        user = next(s for s in py_analysis.files["models.py"].symbols if s.name == "User")
        assert user.base_classes == ["Base"]

    def test_parameters_and_line_ranges(self, py_analysis):
        make_user = next(
            s for s in py_analysis.files["models.py"].symbols if s.name == "make_user"
        )
        assert make_user.params == ["name"]
        assert make_user.start_line < make_user.end_line

    def test_exported_flag_tracks_module_level_public_names(self, py_analysis):
        parsed = py_analysis.files["utils.py"]
        exported = {s.name for s in parsed.symbols if s.is_exported}
        assert exported == {"slugify", "describe"}  # _private_helper is not public

    @pytest.mark.parametrize(
        "source,expected_specifier,expected_level,expected_names",
        [
            (b"import os\n", "os", 0, []),
            (b"import os.path as osp\n", "os.path", 0, []),
            (b"from config import Settings, DB_URL\n", "config", 0, ["Settings", "DB_URL"]),
            (b"from . import sibling\n", "", 1, ["sibling"]),
            (b"from ..pkg.mod import thing\n", "pkg.mod", 2, ["thing"]),
        ],
    )
    def test_import_forms(self, source, expected_specifier, expected_level, expected_names):
        stmt = py_extract("x.py", source).imports[0]
        assert stmt.raw_specifier == expected_specifier
        assert stmt.level == expected_level
        assert stmt.imported_names == expected_names

    def test_import_alias_is_captured(self):
        stmt = py_extract("x.py", b"import os.path as osp\n").imports[0]
        assert stmt.alias == "osp"

    def test_wildcard_import_flagged(self):
        stmt = py_extract("x.py", b"from typing import *\n").imports[0]
        assert stmt.is_wildcard is True

    def test_multiple_imports_on_one_line(self):
        stmts = py_extract("x.py", b"import json, csv\n").imports
        assert [s.raw_specifier for s in stmts] == ["json", "csv"]

    def test_decorators_are_captured(self):
        parsed = py_extract("x.py", b'@app.route("/x")\ndef handler():\n    pass\n')
        assert parsed.symbols[0].decorators == ['app.route("/x")']

    def test_calls_are_attributed_to_enclosing_function(self, py_analysis):
        parsed = py_analysis.files["utils.py"]
        describe = next(s for s in parsed.symbols if s.name == "describe")
        callees = {c.callee_name for c in parsed.calls if c.caller_local_id == describe.local_id}
        assert {"slugify", "get_setting"} <= callees

    def test_module_level_call_has_no_caller(self, py_analysis):
        parsed = py_analysis.files["main.py"]
        assert any(c.callee_name == "main" and c.caller_local_id is None for c in parsed.calls)

    def test_receiver_is_recorded(self, py_analysis):
        parsed = py_analysis.files["app.py"]
        call = next(c for c in parsed.calls if c.callee_name == "connect")
        assert call.receiver == "db"


class TestJsExtraction:
    def test_function_class_and_method_kinds(self, ts_analysis):
        parsed = ts_analysis.files["src/components/Widget.tsx"]
        assert _names(parsed, SymbolKind.CLASS) == ["Widget"]
        assert _names(parsed, SymbolKind.METHOD) == ["render"]
        assert "SmallWidget" in _names(parsed, SymbolKind.FUNCTION)

    def test_class_name_uses_type_identifier_node(self, ts_analysis):
        widget = next(
            s for s in ts_analysis.files["src/components/Widget.tsx"].symbols
            if s.kind is SymbolKind.CLASS
        )
        assert widget.name == "Widget"
        assert widget.base_classes == ["React.Component"]

    def test_arrow_function_takes_name_from_declarator(self, ts_analysis):
        parsed = ts_analysis.files["src/lib/format.ts"]
        assert "shout" in _names(parsed, SymbolKind.FUNCTION)

    def test_expression_bodied_arrow_call_is_captured(self):
        """`const f = x => other(x)` -- the body IS the call node, not a block."""
        parsed = js_extract("x.ts", b"const f = (x) => other(x);\n", Language.TYPESCRIPT)
        assert [c.callee_name for c in parsed.calls] == ["other"]
        assert parsed.calls[0].caller_local_id == parsed.symbols[0].local_id

    def test_export_marks_declaration(self, ts_analysis):
        parsed = ts_analysis.files["src/lib/format.ts"]
        assert all(s.is_exported for s in parsed.symbols if s.name in ("slugify", "shout"))

    def test_import_specifier_forms(self, ts_analysis):
        parsed = ts_analysis.files["src/components/Widget.tsx"]
        by_spec = {i.raw_specifier: i for i in parsed.imports}
        assert by_spec["react"].imported_names == ["React"]
        assert by_spec["../lib/format"].imported_names == ["shout"]

    def test_reexport_is_an_import_edge(self, ts_analysis):
        """`export { x } from "./y"` is a dependency on ./y."""
        parsed = ts_analysis.files["src/lib/index.ts"]
        assert "./format" in {i.raw_specifier for i in parsed.imports}

    def test_commonjs_require_is_an_import(self):
        parsed = js_extract("x.js", b'const a = require("./mod");\n', Language.JAVASCRIPT)
        assert [i.raw_specifier for i in parsed.imports] == ["./mod"]

    def test_tsx_jsx_syntax_parses_without_error(self, ts_analysis):
        assert ts_analysis.files["src/components/Widget.tsx"].parse_error is None


class TestCommonJsExtraction:
    """CommonJS patterns, found on real Express-style codebases.

    Without these, an Express repo yields almost no symbols: its modules assign
    functions to `exports` rather than declaring them.
    """

    def test_exports_assignment_is_a_symbol(self):
        parsed = js_extract(
            "x.js", b"exports.render = function (name) { return name; };\n", Language.JAVASCRIPT
        )
        assert [s.name for s in parsed.symbols] == ["render"]
        assert parsed.symbols[0].is_exported is True

    def test_module_exports_uses_the_inner_function_name(self):
        parsed = js_extract(
            "x.js", b"module.exports = function createApp() { return 1; };\n", Language.JAVASCRIPT
        )
        assert [s.name for s in parsed.symbols] == ["createApp"]

    def test_object_method_assignment_is_a_symbol_but_not_exported(self):
        parsed = js_extract(
            "x.js", b"app.handle = function handle(req) { return req; };\n", Language.JAVASCRIPT
        )
        assert [s.name for s in parsed.symbols] == ["handle"]
        assert parsed.symbols[0].is_exported is False

    def test_calls_inside_an_exports_assignment_are_attributed(self):
        parsed = js_extract(
            "x.js",
            b"exports.render = function (n) { return merge(n); };\n",
            Language.JAVASCRIPT,
        )
        call = parsed.calls[0]
        assert call.callee_name == "merge"
        assert call.caller_local_id == parsed.symbols[0].local_id

    def test_require_binding_name_is_captured_as_alias(self):
        """`var utils = require("./utils")` -- without the alias, a later
        utils.merge() can never be resolved back to ./utils."""
        parsed = js_extract(
            "x.js", b"var utils = require('./utils');\n", Language.JAVASCRIPT
        )
        assert parsed.imports[0].raw_specifier == "./utils"
        assert parsed.imports[0].alias == "utils"

    def test_destructured_require_captures_names(self):
        parsed = js_extract(
            "x.js", b"const { merge, flatten } = require('./utils');\n", Language.JAVASCRIPT
        )
        assert parsed.imports[0].imported_names == ["merge", "flatten"]

    def test_nested_function_is_flagged(self):
        """A decorator's inner `wrapper` is a closure, not dead code."""
        parsed = js_extract(
            "x.js",
            b"function outer() { function wrapper() { return 1; } return wrapper; }\n",
            Language.JAVASCRIPT,
        )
        by_name = {s.name: s for s in parsed.symbols}
        assert by_name["outer"].is_nested is False
        assert by_name["wrapper"].is_nested is True
