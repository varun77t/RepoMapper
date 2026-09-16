"""Resolve JavaScript / TypeScript import specifiers to repo-relative paths.

Implements the subset of Node/TS resolution that actually matters for a
dependency graph:

* relative specifiers (``./x``, ``../x``) with extension and ``/index`` probing
* tsconfig / jsconfig ``baseUrl`` + ``paths`` aliases (``@/lib/format``)
* everything else is a bare specifier -> external package

Aliases are collected from every tsconfig*/jsconfig* in the repo, not just the
shallowest one: Vite's default scaffold puts `paths` in tsconfig.app.json and
leaves tsconfig.json holding nothing but `references`. Each config's aliases
are resolved against its own directory, so a monorepo with several frontends
keeps them separate.

Known limitation: aliases declared only in vite.config / webpack.config are not
read, so those imports fall through to "external". Resolving them would mean
evaluating arbitrary JS config, which is out of scope for static analysis.
"""
from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field

# Probe order matters: a .ts file should win over a stale compiled .js sibling.
_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_INDEX_FILES = tuple(f"index{ext}" for ext in _EXTENSIONS)


@dataclass(slots=True)
class ImportResolution:
    targets: list[str] = field(default_factory=list)
    is_external: bool = False
    external_module: str | None = None


def _strip_json_comments(text: str) -> str:
    """tsconfig.json is JSONC -- comments and trailing commas are legal."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|\s)//[^\n]*", r"\1", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


class JsModuleIndex:
    def __init__(self, all_paths: set[str], config_sources: dict[str, str] | None = None) -> None:
        self._all_paths = all_paths
        # Every alias target is stored already resolved to a repo-relative
        # prefix, so a config nested three directories deep needs no extra
        # bookkeeping at lookup time.
        self._aliases: list[tuple[str, list[str]]] = []
        self._base_urls: list[str] = []
        self._load_config(config_sources or {})

    def _load_config(self, config_sources: dict[str, str]) -> None:
        """Merge baseUrl and paths from every tsconfig/jsconfig, shallowest first."""
        candidates = sorted(config_sources, key=lambda p: (p.count("/"), p))
        seen: set[str] = set()

        for path in candidates:
            try:
                data = json.loads(_strip_json_comments(config_sources[path]))
            except (json.JSONDecodeError, ValueError):
                continue
            opts = (data or {}).get("compilerOptions") or {}
            config_dir = posixpath.dirname(path)
            base_dir = self._join_root(config_dir, opts.get("baseUrl") or ".")

            if opts.get("baseUrl") and base_dir not in self._base_urls:
                self._base_urls.append(base_dir)

            paths = opts.get("paths")
            if not isinstance(paths, dict):
                continue
            for pattern, replacements in paths.items():
                if not isinstance(replacements, list) or pattern in seen:
                    continue
                seen.add(pattern)
                self._aliases.append(
                    (pattern, [self._join_root(base_dir, r) for r in replacements])
                )

        # Longest pattern first, so "@/lib/*" beats "@/*".
        self._aliases.sort(key=lambda kv: len(kv[0]), reverse=True)
        self._base_urls.append("")

    @staticmethod
    def _join_root(root: str, target: str) -> str:
        joined = posixpath.normpath(posixpath.join(root, target))
        return "" if joined in (".", "/") else joined.lstrip("./")

    def _probe(self, candidate: str) -> str | None:
        """Given an extensionless path, find the real file it refers to."""
        candidate = posixpath.normpath(candidate).lstrip("/")
        # `require("../..")` from examples/auth/ normalises to "." -- the repo
        # root, whose index.js is a real target, not a missing path.
        if candidate == ".":
            candidate = ""
        if candidate and candidate in self._all_paths:
            return candidate
        if candidate:
            for ext in _EXTENSIONS:
                probe = candidate + ext
                if probe in self._all_paths:
                    return probe
        for index in _INDEX_FILES:
            probe = posixpath.join(candidate, index) if candidate else index
            if probe in self._all_paths:
                return probe
        return None

    def _resolve_alias(self, specifier: str) -> str | None:
        """Apply tsconfig `paths` patterns, longest prefix first."""
        for pattern, replacements in self._aliases:
            if "*" in pattern:
                prefix = pattern.split("*", 1)[0]
                if not specifier.startswith(prefix):
                    continue
                tail = specifier[len(prefix) :]
            else:
                if specifier != pattern:
                    continue
                tail = ""
            for replacement in replacements:
                target = replacement.replace("*", tail) if "*" in replacement else replacement
                hit = self._probe(target)
                if hit is not None:
                    return hit
        return None

    def resolve(self, importing_path: str, specifier: str) -> ImportResolution:
        if not specifier:
            return ImportResolution(is_external=False)

        if specifier.startswith("."):
            base_dir = posixpath.dirname(importing_path)
            hit = self._probe(posixpath.join(base_dir, specifier))
            if hit is not None and hit != importing_path:
                return ImportResolution(targets=[hit])
            # A relative path that does not exist is broken, not third-party.
            return ImportResolution(is_external=False)

        aliased = self._resolve_alias(specifier)
        if aliased is not None and aliased != importing_path:
            return ImportResolution(targets=[aliased])

        # Bare specifier. It may still be a baseUrl-relative absolute import
        # (`import x from "lib/format"` with baseUrl: "src").
        for base in self._base_urls:
            hit = self._probe(posixpath.join(base, specifier))
            if hit is not None and hit != importing_path:
                return ImportResolution(targets=[hit])

        scope = specifier.split("/")
        module = "/".join(scope[:2]) if specifier.startswith("@") else scope[0]
        return ImportResolution(is_external=True, external_module=module)
