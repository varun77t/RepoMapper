"""Resolve Python import statements to repo-relative file paths.

This is the linchpin of the whole analysis: every file-level graph edge, and
therefore every centrality / community / cycle / reading-order result, depends
on getting this right. Anything that does not resolve to a file inside the repo
is treated as external (stdlib or third-party) and produces no node.

Source roots are inferred rather than assumed: the repo root always counts, plus
`src/`, any directory holding a pyproject.toml / setup.py / setup.cfg, and the
parent of every top-level package. That last rule is what makes an application
laid out as `backend/app/...` work -- `import app.config` is only resolvable if
`backend/` is understood to be on the path, and such a directory frequently
carries no packaging metadata at all.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

_ROOT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


@dataclass(slots=True)
class ImportResolution:
    targets: list[str] = field(default_factory=list)
    is_external: bool = False
    external_module: str | None = None


class PythonModuleIndex:
    """Maps dotted module paths to repo-relative file paths."""

    def __init__(self, all_paths: set[str]) -> None:
        self._all_paths = all_paths
        self._roots = self._infer_roots(all_paths)
        self._modules: dict[str, str] = {}
        self._packages: set[str] = set()
        self._build(all_paths)

    @staticmethod
    def _infer_roots(all_paths: set[str]) -> list[str]:
        roots = {""}
        package_dirs = set()
        for path in all_paths:
            parent = posixpath.dirname(path)
            base = posixpath.basename(path)
            if base in _ROOT_MARKERS:
                roots.add(parent)
            if base == "__init__.py":
                package_dirs.add(parent)

        # The directory above a top-level package is on sys.path when that
        # package is imported by its own name. `backend/app/__init__.py` with
        # no `backend/__init__.py` means `backend/` is a source root and the
        # module is `app.config`, not `backend.app.config`. Without this, a
        # FastAPI or Django app that is not at the repo root resolves none of
        # its own imports and the graph comes back with almost no edges.
        for directory in package_dirs:
            parent = posixpath.dirname(directory)
            if parent not in package_dirs:
                roots.add(parent)

        if any(p.startswith("src/") for p in all_paths):
            roots.add("src")
        # Longest first: "src/pkg" must win over "" for src/pkg/mod.py.
        return sorted(roots, key=len, reverse=True)

    def _build(self, all_paths: set[str]) -> None:
        for path in sorted(all_paths):
            if not path.endswith(".py"):
                continue
            for root in self._roots:
                rel = self._strip_root(path, root)
                if rel is None:
                    continue
                parts = rel[: -len(".py")].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                    if not parts:
                        continue
                    self._packages.add(".".join(parts))
                dotted = ".".join(parts)
                # First root wins (longest), so do not overwrite.
                self._modules.setdefault(dotted, path)

    @staticmethod
    def _strip_root(path: str, root: str) -> str | None:
        if not root:
            return path
        prefix = root + "/"
        return path[len(prefix) :] if path.startswith(prefix) else None

    def lookup(self, dotted: str) -> str | None:
        return self._modules.get(dotted)

    def package_of(self, path: str) -> str:
        """Dotted package a file lives in, used as the base for relative imports."""
        for root in self._roots:
            rel = self._strip_root(path, root)
            if rel is None:
                continue
            parts = rel[: -len(".py")].split("/") if rel.endswith(".py") else rel.split("/")
            # A package's __init__.py is *inside* the package it names.
            if parts and parts[-1] != "__init__":
                parts = parts[:-1]
            else:
                parts = parts[:-1]
            return ".".join(parts)
        return ""

    def resolve(
        self, importing_path: str, specifier: str, level: int, imported_names: list[str]
    ) -> ImportResolution:
        """Resolve one import statement.

        `from a.b import c` is ambiguous: `c` may be a submodule (-> a/b/c.py)
        or a symbol inside a/b.py. Both are resolved when they exist, since both
        are genuine dependencies of the importing file.
        """
        if level > 0:
            base = self.package_of(importing_path)
            base_parts = base.split(".") if base else []
            # level=1 is the current package; each extra dot goes up one.
            if level - 1 > 0:
                base_parts = base_parts[: -(level - 1)] if level - 1 <= len(base_parts) else []
            dotted = ".".join([p for p in base_parts if p] + ([specifier] if specifier else []))
        else:
            dotted = specifier

        if not dotted:
            return ImportResolution(is_external=False)

        targets: list[str] = []
        module_path = self.lookup(dotted)
        if module_path is not None and module_path != importing_path:
            targets.append(module_path)

        # Submodule imports: `from pkg import mod` -> pkg/mod.py
        for name in imported_names:
            sub = self.lookup(f"{dotted}.{name}")
            if sub is not None and sub != importing_path and sub not in targets:
                targets.append(sub)

        if targets:
            return ImportResolution(targets=targets)

        if level > 0:
            # A relative import that resolved to nothing is a broken/uncovered
            # path, not a third-party package -- do not call it external.
            return ImportResolution(is_external=False)

        return ImportResolution(is_external=True, external_module=dotted.split(".")[0])
