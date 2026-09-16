"""Discover analyzable source files inside a cloned repo."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.parsing.grammars import language_for_path
from app.parsing.types import Language

# Directories that are never worth parsing. Matched on any path segment.
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", ".env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "dist", "build", "out", ".next", ".nuxt", ".output", "coverage",
    "site-packages", "vendor", "bower_components", ".idea", ".vscode",
    "migrations", ".gradle", "target", "bin", "obj",
})

# Vite scaffolds a tsconfig.json that holds nothing but `references` to
# tsconfig.app.json and tsconfig.node.json, and the path aliases live in the
# referenced file. Capturing only the two canonical names misses the alias
# table on most modern React projects, so any tsconfig*/jsconfig* is collected.
_CONFIG_PREFIXES = ("tsconfig", "jsconfig")


def is_config_filename(filename: str) -> bool:
    return filename.endswith(".json") and filename.startswith(_CONFIG_PREFIXES)

# Average bytes-per-line above this means a minified or generated bundle.
MINIFIED_AVG_LINE_BYTES = 500


@dataclass(slots=True)
class DiscoveredFile:
    rel_path: str
    abs_path: Path
    language: Language
    size_bytes: int


@dataclass(slots=True)
class WalkResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    configs: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    def note_skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _is_minified(path: Path, size: int) -> bool:
    name = path.name
    if ".min." in name or name.endswith((".bundle.js", ".pack.js")):
        return True
    if size < 4000:
        return False
    try:
        with path.open("rb") as fh:
            newlines = fh.read(200_000).count(b"\n")
    except OSError:
        return False
    return size / max(newlines, 1) > MINIFIED_AVG_LINE_BYTES


def walk_repo(root: Path, max_files: int, max_file_bytes: int) -> WalkResult:
    result = WalkResult()
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        # Pruning in place stops os.walk descending into ignored trees at all.
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        current = Path(dirpath)

        for filename in sorted(filenames):
            abs_path = current / filename
            try:
                rel_path = abs_path.relative_to(root).as_posix()
            except ValueError:
                continue

            if is_config_filename(filename):
                try:
                    result.configs[rel_path] = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
                continue

            language = language_for_path(filename)
            if language is None:
                continue

            try:
                size = abs_path.stat().st_size
            except OSError:
                result.note_skip("stat_failed")
                continue

            if size > max_file_bytes:
                result.note_skip("too_large")
                continue
            if _is_minified(abs_path, size):
                result.note_skip("minified")
                continue

            if len(result.files) >= max_files:
                result.truncated = True
                result.note_skip("file_limit")
                continue

            result.files.append(
                DiscoveredFile(
                    rel_path=rel_path, abs_path=abs_path, language=language, size_bytes=size
                )
            )

    result.files.sort(key=lambda f: f.rel_path)
    return result
