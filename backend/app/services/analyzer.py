"""Parse a directory into a complete, database-free analysis result.

This is the seam that makes the pipeline testable: it takes a path on disk and
returns plain dataclasses. No SQLAlchemy, no FastAPI, no network. The graph
builder and the algorithms consume its output; the persistence layer stores it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion.walker import WalkResult, walk_repo
from app.parsing import js_extractor, python_extractor
from app.parsing.resolution.calls import CallResolver, ResolvedCall, resolution_stats
from app.parsing.resolution.js_imports import JsModuleIndex
from app.parsing.resolution.python_imports import PythonModuleIndex
from app.parsing.types import ImportStmt, Language, ParsedFile


@dataclass(slots=True)
class ResolvedImport:
    source_path: str
    raw_specifier: str
    imported_names: list[str]
    line: int
    target_path: str | None = None
    is_external: bool = False
    external_module: str | None = None


@dataclass(slots=True)
class RepoAnalysis:
    files: dict[str, ParsedFile] = field(default_factory=dict)
    imports: list[ResolvedImport] = field(default_factory=list)
    calls: list[ResolvedCall] = field(default_factory=list)
    call_stats: dict = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    @property
    def import_edges(self) -> list[tuple[str, str]]:
        """(importer, imported) pairs for files inside the repo.

        Direction is a contract: A -> B means A imports B, so heavily depended
        upon files accumulate *incoming* edges and rank highly under PageRank
        without reversing the graph.
        """
        return [
            (imp.source_path, imp.target_path)
            for imp in self.imports
            if imp.target_path is not None
        ]


def _parse_files(walk: WalkResult) -> dict[str, ParsedFile]:
    parsed: dict[str, ParsedFile] = {}
    for discovered in walk.files:
        try:
            source = discovered.abs_path.read_bytes()
        except OSError as exc:
            parsed[discovered.rel_path] = ParsedFile(
                path=discovered.rel_path,
                language=discovered.language,
                parse_error=f"read failed: {exc}",
            )
            continue

        if discovered.language is Language.PYTHON:
            result = python_extractor.extract(discovered.rel_path, source)
        else:
            result = js_extractor.extract(discovered.rel_path, source, discovered.language)
        parsed[discovered.rel_path] = result
    return parsed


def _resolve_imports(
    parsed: dict[str, ParsedFile], configs: dict[str, str]
) -> tuple[list[ResolvedImport], dict[str, list[tuple[ImportStmt, list[str]]]]]:
    all_paths = set(parsed)
    py_index = PythonModuleIndex(all_paths)
    js_index = JsModuleIndex(all_paths, configs)

    resolved: list[ResolvedImport] = []
    per_file: dict[str, list[tuple[ImportStmt, list[str]]]] = {}

    for path, parsed_file in parsed.items():
        entries: list[tuple[ImportStmt, list[str]]] = []
        for stmt in parsed_file.imports:
            if parsed_file.language is Language.PYTHON:
                outcome = py_index.resolve(
                    path, stmt.raw_specifier, stmt.level, stmt.imported_names
                )
            else:
                outcome = js_index.resolve(path, stmt.raw_specifier)

            entries.append((stmt, outcome.targets))

            if outcome.targets:
                for target in outcome.targets:
                    resolved.append(
                        ResolvedImport(
                            source_path=path,
                            raw_specifier=stmt.raw_specifier,
                            imported_names=list(stmt.imported_names),
                            line=stmt.line,
                            target_path=target,
                        )
                    )
            else:
                resolved.append(
                    ResolvedImport(
                        source_path=path,
                        raw_specifier=stmt.raw_specifier,
                        imported_names=list(stmt.imported_names),
                        line=stmt.line,
                        is_external=outcome.is_external,
                        external_module=outcome.external_module,
                    )
                )
        per_file[path] = entries

    return resolved, per_file


def analyze_directory(root: Path, max_files: int = 5000, max_file_bytes: int = 1_000_000) -> RepoAnalysis:
    walk = walk_repo(root, max_files=max_files, max_file_bytes=max_file_bytes)
    parsed = _parse_files(walk)
    imports, per_file = _resolve_imports(parsed, walk.configs)

    resolver = CallResolver(parsed, per_file)
    calls = resolver.resolve_all()

    return RepoAnalysis(
        files=parsed,
        imports=imports,
        calls=calls,
        call_stats=resolution_stats(calls),
        skipped=dict(walk.skipped),
        truncated=walk.truncated,
    )
