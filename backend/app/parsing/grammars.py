"""tree-sitter grammar registry.

Parsers are built once and reused -- constructing a Parser per file is wasteful
on large repos.

Version note: the grammar packages are pinned as a *set* (see requirements.txt).
tree-sitter is backwards- but not forwards-compatible on grammar ABI, so mixing
a 0.23 core with 0.25 grammars fails at load time.
"""
from __future__ import annotations

from functools import lru_cache

import tree_sitter_javascript as ts_javascript
import tree_sitter_python as ts_python
import tree_sitter_typescript as ts_typescript
from tree_sitter import Language as TSLanguage
from tree_sitter import Parser

from app.parsing.types import Language

EXTENSION_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".jsx": Language.JSX,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TSX,
}

_GRAMMAR_FACTORY = {
    Language.PYTHON: ts_python.language,
    Language.JAVASCRIPT: ts_javascript.language,
    # .jsx is parsed by the tsx grammar: the plain javascript grammar in this
    # release does not accept JSX syntax cleanly.
    Language.JSX: ts_typescript.language_tsx,
    Language.TYPESCRIPT: ts_typescript.language_typescript,
    Language.TSX: ts_typescript.language_tsx,
}

SUPPORTED_EXTENSIONS = frozenset(EXTENSION_MAP)


def language_for_path(path: str) -> Language | None:
    dot = path.rfind(".")
    if dot == -1:
        return None
    return EXTENSION_MAP.get(path[dot:].lower())


@lru_cache(maxsize=None)
def get_parser(language: Language) -> Parser:
    return Parser(TSLanguage(_GRAMMAR_FACTORY[language]()))
