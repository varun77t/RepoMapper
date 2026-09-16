"""Extract symbols, imports and call sites from JavaScript / TypeScript ASTs.

Covers .js/.mjs/.cjs/.jsx/.ts/.tsx via three grammars (javascript, typescript,
tsx). Node types differ from Python in ways worth naming:

* a class name is a ``type_identifier``, not an ``identifier``
* base classes live under ``class_heritage > extends_clause``
* ``const f = () => {}`` has no name on the function node -- the name comes from
  the enclosing ``variable_declarator``
* ``export { x } from "./y"`` is a re-export, which is also an import edge
* CommonJS assigns functions rather than declaring them
  (``exports.render = function () {}``), so the assignment target is the only
  place the name exists -- without handling it, most of an Express-style
  codebase has no symbols at all
"""
from __future__ import annotations

from tree_sitter import Node

from app.parsing.grammars import get_parser
from app.parsing.types import CallExpr, ImportStmt, Language, ParsedFile, SymbolDef, SymbolKind

_FUNCTION_VALUE_TYPES = {"arrow_function", "function_expression", "function"}
_PARAM_TYPES = {"required_parameter", "optional_parameter"}


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _string_literal_value(src: bytes, node: Node | None) -> str:
    """Pull the text out of a `string` node via its string_fragment child."""
    if node is None:
        return ""
    frag = next((c for c in node.named_children if c.type == "string_fragment"), None)
    if frag is not None:
        return _text(src, frag)
    return _text(src, node).strip("\"'`")


def _param_names(src: bytes, params: Node | None) -> list[str]:
    if params is None:
        return []
    names: list[str] = []
    for child in params.named_children:
        if child.type == "identifier":
            names.append(_text(src, child))
        elif child.type in _PARAM_TYPES:
            pattern = child.child_by_field_name("pattern")
            if pattern is not None:
                names.append(_text(src, pattern))
        elif child.type in ("rest_pattern", "assignment_pattern"):
            ident = next((c for c in child.named_children if c.type == "identifier"), None)
            if ident is not None:
                names.append(_text(src, ident))
    return names


class _JsWalker:
    def __init__(self, src: bytes, parsed: ParsedFile) -> None:
        self.src = src
        self.parsed = parsed
        self._next_local_id = 0

    def _new_symbol(self, **kwargs) -> SymbolDef:
        sym = SymbolDef(local_id=self._next_local_id, **kwargs)
        self._next_local_id += 1
        self.parsed.symbols.append(sym)
        return sym

    def _add_import(
        self, specifier: str, names: list[str], line: int, alias: str | None = None
    ) -> None:
        if specifier:
            self.parsed.imports.append(
                ImportStmt(
                    raw_specifier=specifier,
                    imported_names=names,
                    line=line,
                    level=0,
                    alias=alias,
                )
            )

    def _require_specifier(self, node: Node) -> str | None:
        """The string argument of a require()/import() call, if this is one."""
        if node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is None or _text(self.src, fn) not in ("require", "import"):
            return None
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        arg = next(
            (c for c in args.named_children if c.type in ("string", "template_string")), None
        )
        return _string_literal_value(self.src, arg) if arg is not None else None

    # --- imports ---------------------------------------------------------
    def _handle_import(self, node: Node) -> None:
        specifier = _string_literal_value(self.src, node.child_by_field_name("source"))
        names: list[str] = []
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is not None:
            for child in clause.named_children:
                if child.type == "identifier":  # default import
                    names.append(_text(self.src, child))
                elif child.type == "named_imports":
                    for spec in child.named_children:
                        if spec.type == "import_specifier":
                            alias = spec.child_by_field_name("alias")
                            name = spec.child_by_field_name("name")
                            target = alias if alias is not None else name
                            if target is not None:
                                names.append(_text(self.src, target))
                elif child.type == "namespace_import":  # import * as ns
                    ident = next(
                        (c for c in child.named_children if c.type == "identifier"), None
                    )
                    if ident is not None:
                        names.append(_text(self.src, ident))
        self._add_import(specifier, names, node.start_point[0] + 1)

    def _handle_require(self, node: Node) -> bool:
        """CommonJS `require("x")` and dynamic `import("x")`. Returns True if matched."""
        fn = node.child_by_field_name("function")
        if fn is None:
            return False
        fn_text = _text(self.src, fn)
        if fn_text not in ("require", "import"):
            return False
        args = node.child_by_field_name("arguments")
        if args is None:
            return False
        arg = next((c for c in args.named_children if c.type in ("string", "template_string")), None)
        if arg is None:
            return False
        self._add_import(_string_literal_value(self.src, arg), [], node.start_point[0] + 1)
        return True

    # --- calls -----------------------------------------------------------
    def _handle_call(self, node: Node, caller: int | None) -> None:
        if self._handle_require(node):
            return
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        if fn.type == "identifier":
            name, receiver = _text(self.src, fn), None
        elif fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            obj = fn.child_by_field_name("object")
            if prop is None:
                return
            name = _text(self.src, prop)
            receiver = _text(self.src, obj)[:120] if obj is not None else None
        else:
            return
        self.parsed.calls.append(
            CallExpr(
                callee_name=name,
                line=node.start_point[0] + 1,
                caller_local_id=caller,
                receiver=receiver,
            )
        )

    # --- definitions -----------------------------------------------------
    def _add_function(
        self,
        name: str,
        node: Node,
        params_node: Node | None,
        body: Node | None,
        class_id: int | None,
        class_name: str | None,
        exported: bool,
        depth: int,
    ) -> None:
        sym = self._new_symbol(
            kind=SymbolKind.METHOD if class_id is not None else SymbolKind.FUNCTION,
            name=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            params=_param_names(self.src, params_node),
            parent_local_id=class_id,
            qualified_name=f"{class_name}.{name}" if class_name else name,
            is_exported=exported,
            is_nested=depth > 0 and class_id is None,
        )
        self._walk_body(body, sym.local_id, depth)

    def _walk_body(self, body: Node | None, caller: int, depth: int) -> None:
        """Walk a function body.

        An expression-bodied arrow (`x => other(x)`) has the call_expression
        itself as the body, and _walk only visits *children* -- so that call
        would be dropped unless the body node is handled directly.
        """
        if body is None:
            return
        if body.type == "call_expression":
            self._handle_call(body, caller)
        self._walk(body, caller=caller, class_id=None, class_name=None,
                   exported=False, depth=depth + 1)

    def _handle_class(self, node: Node, exported: bool, depth: int) -> None:
        name_node = node.child_by_field_name("name")
        name = _text(self.src, name_node) if name_node is not None else "(anonymous)"
        bases: list[str] = []
        heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
        if heritage is not None:
            for clause in heritage.named_children:
                if clause.type == "extends_clause":
                    value = clause.child_by_field_name("value")
                    if value is not None:
                        bases.append(_text(self.src, value))
                    else:
                        bases.extend(
                            _text(self.src, c)
                            for c in clause.named_children
                            if c.type in ("identifier", "member_expression")
                        )
        sym = self._new_symbol(
            kind=SymbolKind.CLASS,
            name=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            base_classes=bases,
            qualified_name=name,
            is_exported=exported,
        )
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk(body, caller=None, class_id=sym.local_id, class_name=name,
                       exported=False, depth=depth + 1)

    def _handle_variable_declarator(
        self, node: Node, caller: int | None, exported: bool, depth: int
    ) -> bool:
        """`const f = () => {}` -- name comes from the declarator, not the function.

        Also the CommonJS binding site: `const utils = require("./utils")` needs
        the name `utils` recorded as the module alias, otherwise a later
        `utils.foo()` cannot be resolved back to ./utils at all.
        """
        value = node.child_by_field_name("value")
        name_node = node.child_by_field_name("name")
        if value is None or name_node is None:
            return False

        specifier = self._require_specifier(value)
        if specifier is not None:
            alias, names = None, []
            if name_node.type == "identifier":
                alias = _text(self.src, name_node)
            elif name_node.type == "object_pattern":
                # const { a, b } = require("./y")
                for child in name_node.named_children:
                    target = child.child_by_field_name("value") or child
                    if target.type == "identifier":
                        names.append(_text(self.src, target))
                    elif child.type == "shorthand_property_identifier_pattern":
                        names.append(_text(self.src, child))
            self._add_import(specifier, names, node.start_point[0] + 1, alias=alias)
            return True

        if value.type not in _FUNCTION_VALUE_TYPES:
            return False
        self._add_function(
            name=_text(self.src, name_node),
            node=node,
            params_node=value.child_by_field_name("parameters"),
            body=value.child_by_field_name("body"),
            class_id=None,
            class_name=None,
            exported=exported,
            depth=depth,
        )
        return True

    def _handle_assignment(self, node: Node, depth: int) -> bool:
        """`exports.render = function () {}` / `app.handle = function () {}`.

        The CommonJS equivalent of a function declaration. The name lives on the
        assignment target, and an `exports.`/`module.exports` target is the
        module's public surface.
        """
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None or right.type not in _FUNCTION_VALUE_TYPES:
            return False

        if left.type == "member_expression":
            prop = left.child_by_field_name("property")
            obj = left.child_by_field_name("object")
            if prop is None:
                return False
            name = _text(self.src, prop)
            owner = _text(self.src, obj) if obj is not None else ""
            exported = owner in ("exports", "module") or owner.endswith("exports")
            # `module.exports = function createApp() {}` -- prefer the real name
            # over the meaningless property name "exports".
            if name == "exports":
                inner = right.child_by_field_name("name")
                name = _text(self.src, inner) if inner is not None else "module.exports"
        elif left.type == "identifier":
            name = _text(self.src, left)
            exported = False
        else:
            return False

        self._add_function(
            name=name,
            node=node,
            params_node=right.child_by_field_name("parameters"),
            body=right.child_by_field_name("body"),
            class_id=None,
            class_name=None,
            exported=exported,
            depth=depth,
        )
        return True

    # --- traversal -------------------------------------------------------
    def walk(self, node: Node) -> None:
        self._walk(node, caller=None, class_id=None, class_name=None, exported=False, depth=0)

    def _walk(
        self,
        node: Node,
        caller: int | None,
        class_id: int | None,
        class_name: str | None,
        exported: bool,
        depth: int,
    ) -> None:
        for child in node.named_children:
            t = child.type

            if t == "import_statement":
                self._handle_import(child)
                continue

            if t == "export_statement":
                # `export { x } from "./y"` is a re-export: also an import edge.
                source = child.child_by_field_name("source")
                if source is not None:
                    self._add_import(
                        _string_literal_value(self.src, source), [], child.start_point[0] + 1
                    )
                self._walk(child, caller, class_id, class_name, exported=True, depth=depth)
                continue

            if t in ("function_declaration", "generator_function_declaration"):
                name_node = child.child_by_field_name("name")
                self._add_function(
                    name=_text(self.src, name_node) if name_node is not None else "default",
                    node=child,
                    params_node=child.child_by_field_name("parameters"),
                    body=child.child_by_field_name("body"),
                    class_id=class_id,
                    class_name=class_name,
                    exported=exported,
                    depth=depth,
                )
                continue

            if t in ("class_declaration", "class"):
                self._handle_class(child, exported, depth)
                continue

            if t == "method_definition":
                name_node = child.child_by_field_name("name")
                self._add_function(
                    name=_text(self.src, name_node) if name_node is not None else "(anonymous)",
                    node=child,
                    params_node=child.child_by_field_name("parameters"),
                    body=child.child_by_field_name("body"),
                    class_id=class_id,
                    class_name=class_name,
                    exported=False,
                    depth=depth,
                )
                continue

            if t == "variable_declarator" and self._handle_variable_declarator(
                child, caller, exported, depth
            ):
                continue

            if t == "assignment_expression" and self._handle_assignment(child, depth):
                continue

            if t == "call_expression":
                self._handle_call(child, caller)
                # fall through: arguments may contain further calls

            # `export` only marks its own declaration, not arbitrary descendants.
            self._walk(
                child,
                caller,
                class_id,
                class_name,
                exported=exported and t in ("lexical_declaration", "variable_declaration"),
                depth=depth,
            )


def extract(path: str, source: bytes, language: Language) -> ParsedFile:
    parsed = ParsedFile(
        path=path,
        language=language,
        line_count=source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0),
        size_bytes=len(source),
    )
    try:
        tree = get_parser(language).parse(source)
    except Exception as exc:  # pragma: no cover - defensive
        parsed.parse_error = f"{type(exc).__name__}: {exc}"
        return parsed
    _JsWalker(source, parsed).walk(tree.root_node)
    return parsed
