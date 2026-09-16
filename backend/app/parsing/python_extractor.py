"""Extract symbols, imports and call sites from a Python AST.

Uses explicit node-type traversal rather than the tree-sitter query API: the
query API changed shape three times between 0.22 and 0.25 (tuple captures ->
dict captures -> Query/QueryCursor split), whereas node types are stable.
"""
from __future__ import annotations

from tree_sitter import Node

from app.parsing.grammars import get_parser
from app.parsing.types import CallExpr, ImportStmt, Language, ParsedFile, SymbolDef, SymbolKind

# Parameter nodes whose "name" field (or first identifier) holds the real name.
_PARAM_WRAPPERS = {
    "default_parameter",
    "typed_parameter",
    "typed_default_parameter",
    "list_splat_pattern",
    "dictionary_splat_pattern",
}


def _text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _param_names(src: bytes, params: Node | None) -> list[str]:
    if params is None:
        return []
    names: list[str] = []
    for child in params.named_children:
        if child.type == "identifier":
            names.append(_text(src, child))
        elif child.type in _PARAM_WRAPPERS:
            name_node = child.child_by_field_name("name")
            if name_node is None:
                name_node = next(
                    (c for c in child.named_children if c.type == "identifier"), None
                )
            if name_node is not None:
                names.append(_text(src, name_node))
    return names


def _import_prefix_level(node: Node) -> int:
    """Leading-dot count of a relative import: from ..pkg import x -> 2."""
    prefix = next((c for c in node.children if c.type == "import_prefix"), None)
    return len(prefix.text.decode()) if prefix is not None else 0


class _PythonWalker:
    def __init__(self, src: bytes, parsed: ParsedFile) -> None:
        self.src = src
        self.parsed = parsed
        self._next_local_id = 0

    def _new_symbol(self, **kwargs) -> SymbolDef:
        sym = SymbolDef(local_id=self._next_local_id, **kwargs)
        self._next_local_id += 1
        self.parsed.symbols.append(sym)
        return sym

    # --- imports ---------------------------------------------------------
    def _handle_import(self, node: Node) -> None:
        """import a.b.c / import a as b / import x, y"""
        for child in node.children_by_field_name("name"):
            alias = None
            if child.type == "aliased_import":
                target = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                module = _text(self.src, target) if target is not None else ""
                alias = _text(self.src, alias_node) if alias_node is not None else None
            else:
                module = _text(self.src, child)
            if module:
                self.parsed.imports.append(
                    ImportStmt(
                        raw_specifier=module,
                        imported_names=[],
                        line=node.start_point[0] + 1,
                        level=0,
                        alias=alias,
                    )
                )

    def _handle_import_from(self, node: Node) -> None:
        """from a.b import c / from . import x / from ..p import y / from m import *"""
        module_node = node.child_by_field_name("module_name")
        level = 0
        module = ""
        if module_node is not None:
            if module_node.type == "relative_import":
                level = _import_prefix_level(module_node)
                dotted = next(
                    (c for c in module_node.named_children if c.type == "dotted_name"), None
                )
                module = _text(self.src, dotted) if dotted is not None else ""
            else:
                module = _text(self.src, module_node)

        is_wildcard = any(c.type == "wildcard_import" for c in node.named_children)
        # `from . import json as json` -- the name child is an aliased_import,
        # whose raw text ("json as json") would never match a module.
        names = []
        for name_node in node.children_by_field_name("name"):
            if name_node.type == "aliased_import":
                original = name_node.child_by_field_name("name")
                if original is not None:
                    names.append(_text(self.src, original))
            else:
                names.append(_text(self.src, name_node))

        self.parsed.imports.append(
            ImportStmt(
                raw_specifier=module,
                imported_names=names,
                line=node.start_point[0] + 1,
                level=level,
                is_wildcard=is_wildcard,
            )
        )

    # --- calls -----------------------------------------------------------
    def _handle_call(self, node: Node, caller: int | None) -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        if fn.type == "identifier":
            name, receiver = _text(self.src, fn), None
        elif fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            obj = fn.child_by_field_name("object")
            if attr is None:
                return
            name = _text(self.src, attr)
            # Chained/literal receivers (`{...}.get()`) are unresolvable noise;
            # keep them for display but never let them bloat the row.
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
    def _handle_function(
        self,
        node: Node,
        caller: int | None,
        class_id: int | None,
        class_name: str | None,
        decorators: list[str],
        depth: int,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(self.src, name_node)
        sym = self._new_symbol(
            kind=SymbolKind.METHOD if class_id is not None else SymbolKind.FUNCTION,
            name=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            params=_param_names(self.src, node.child_by_field_name("parameters")),
            decorators=decorators,
            parent_local_id=class_id,
            qualified_name=f"{class_name}.{name}" if class_name else name,
            # Python has no export keyword; a public module-level name is the
            # closest equivalent, which is what orphan detection needs.
            is_exported=depth == 0 and not name.startswith("_"),
            is_nested=depth > 0 and class_id is None,
        )
        body = node.child_by_field_name("body")
        if body is not None:
            # Nested defs keep FUNCTION kind but are attributed to this caller.
            self._walk(body, caller=sym.local_id, class_id=None, class_name=None, depth=depth + 1)

    def _handle_class(self, node: Node, decorators: list[str], depth: int) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(self.src, name_node)
        bases: list[str] = []
        supers = node.child_by_field_name("superclasses")
        if supers is not None:
            bases = [
                _text(self.src, c)
                for c in supers.named_children
                if c.type in ("identifier", "attribute")
            ]
        sym = self._new_symbol(
            kind=SymbolKind.CLASS,
            name=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            base_classes=bases,
            decorators=decorators,
            qualified_name=name,
            is_exported=depth == 0 and not name.startswith("_"),
        )
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk(body, caller=None, class_id=sym.local_id, class_name=name, depth=depth + 1)

    # --- traversal -------------------------------------------------------
    def walk(self, node: Node) -> None:
        self._walk(node, caller=None, class_id=None, class_name=None, depth=0)

    def _walk(
        self,
        node: Node,
        caller: int | None,
        class_id: int | None,
        class_name: str | None,
        depth: int,
    ) -> None:
        for child in node.named_children:
            t = child.type

            if t == "decorated_definition":
                decs = [
                    _text(self.src, d).lstrip("@").strip()
                    for d in child.named_children
                    if d.type == "decorator"
                ]
                inner = child.child_by_field_name("definition")
                if inner is None:
                    continue
                if inner.type == "function_definition":
                    self._handle_function(inner, caller, class_id, class_name, decs, depth)
                elif inner.type == "class_definition":
                    self._handle_class(inner, decs, depth)
                continue

            if t == "function_definition":
                self._handle_function(child, caller, class_id, class_name, [], depth)
                continue
            if t == "class_definition":
                self._handle_class(child, [], depth)
                continue
            if t == "import_statement":
                self._handle_import(child)
                continue
            if t == "import_from_statement":
                self._handle_import_from(child)
                continue
            if t == "call":
                self._handle_call(child, caller)
                # fall through: arguments may contain further calls

            self._walk(child, caller, class_id, class_name, depth)


def extract(path: str, source: bytes) -> ParsedFile:
    parsed = ParsedFile(
        path=path,
        language=Language.PYTHON,
        line_count=source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0),
        size_bytes=len(source),
    )
    try:
        tree = get_parser(Language.PYTHON).parse(source)
    except Exception as exc:  # pragma: no cover - defensive
        parsed.parse_error = f"{type(exc).__name__}: {exc}"
        return parsed
    _PythonWalker(source, parsed).walk(tree.root_node)
    return parsed
