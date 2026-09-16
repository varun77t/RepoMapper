"""Language-neutral extraction results.

These dataclasses are the contract between the parsing layer and everything
downstream. Extractors produce them; the graph builder and the persistence
layer consume them. Nothing here knows about tree-sitter, SQLAlchemy or HTTP.

Symbols are identified *within a file* by ``local_id`` (a simple index), because
database ids do not exist yet at extraction time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    JSX = "jsx"

    @property
    def is_js_family(self) -> bool:
        return self is not Language.PYTHON


class SymbolKind(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"


@dataclass(slots=True)
class SymbolDef:
    local_id: int
    kind: SymbolKind
    name: str
    start_line: int
    end_line: int
    params: list[str] = field(default_factory=list)
    base_classes: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    parent_local_id: int | None = None  # methods -> owning class
    is_exported: bool = False
    # Defined inside another function (a closure or callback). Such symbols
    # escape by being returned or passed, never by being called by name.
    is_nested: bool = False
    # "Klass.method" for methods, bare name otherwise. Set by the extractor,
    # which is the only place that knows the enclosing scope.
    qualified_name: str = ""

    def __post_init__(self) -> None:
        if not self.qualified_name:
            self.qualified_name = self.name


@dataclass(slots=True)
class ImportStmt:
    """One import statement.

    ``raw_specifier`` is the module text as written (``os.path``, ``./utils``).
    ``level`` is the number of leading dots for Python relative imports (0 for
    absolute); JS relative-ness is encoded in the specifier itself.
    """

    raw_specifier: str
    imported_names: list[str] = field(default_factory=list)
    line: int = 0
    level: int = 0
    is_wildcard: bool = False
    # Local binding for `import x as y` / `import * as y`. Call resolution needs
    # it to match a receiver (`y.foo()`) back to the imported module.
    alias: str | None = None


@dataclass(slots=True)
class CallExpr:
    """A call site.

    ``caller_local_id`` is None for calls made at module scope.
    ``receiver`` is the object a method was called on (``self``, ``this``, or a
    variable name), or None for a bare ``foo()`` call.
    """

    callee_name: str
    line: int
    caller_local_id: int | None = None
    receiver: str | None = None


@dataclass(slots=True)
class ParsedFile:
    path: str  # repo-relative, posix separators
    language: Language
    line_count: int = 0
    size_bytes: int = 0
    symbols: list[SymbolDef] = field(default_factory=list)
    imports: list[ImportStmt] = field(default_factory=list)
    calls: list[CallExpr] = field(default_factory=list)
    parse_error: str | None = None

    def symbol_by_local_id(self, local_id: int) -> SymbolDef | None:
        for s in self.symbols:
            if s.local_id == local_id:
                return s
        return None
