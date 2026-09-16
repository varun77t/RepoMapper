"""Best-effort call resolution.

Resolving every call correctly across a whole repo is a language-server-grade
problem (it needs real type inference). This deliberately does not attempt that.
Instead it resolves in confidence-ordered tiers and *records which tier fired*,
so consumers can filter by trust level and the reported accuracy is honest:

    local      1.0   same file, or self/this method on the enclosing class
    import     0.9   name came from an import that resolved to a repo file
    heuristic  0.5   name is defined exactly once in the entire repo
    builtin     -    a language/runtime builtin (print, strip, toLowerCase)
    unresolved  -    not found, or ambiguous (defined in several places)

Builtins are split out from genuine failures on purpose. Counting `print()` as
an unresolved call would understate accuracy badly -- it is not a repo symbol
and never could resolve. `resolved_pct` is therefore measured over in-repo
calls only, with the builtin count reported separately.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from app.parsing.types import ParsedFile, SymbolDef, SymbolKind

SymbolRef = tuple[str, int]  # (file path, symbol local_id)

_SELF_RECEIVERS = frozenset({"self", "this"})

# Not exhaustive, and does not need to be: it only has to catch the common
# language/runtime names so they are not miscounted as resolution failures.
_BUILTINS = frozenset({
    # Python builtins
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
    "range", "enumerate", "zip", "map", "filter", "sorted", "reversed", "sum",
    "min", "max", "abs", "round", "open", "isinstance", "issubclass", "getattr",
    "setattr", "hasattr", "delattr", "super", "type", "repr", "format", "vars",
    "dir", "id", "hash", "iter", "next", "any", "all", "bytes", "frozenset",
    # Python str/list/dict methods
    "strip", "lstrip", "rstrip", "split", "rsplit", "join", "replace", "lower",
    "upper", "title", "startswith", "endswith", "find", "index", "count",
    "append", "extend", "insert", "remove", "pop", "clear", "copy", "keys",
    "values", "items", "get", "update", "setdefault", "sort", "encode", "decode",
    # JS/TS globals and common methods
    "require", "parseInt", "parseFloat", "isNaN", "fetch", "setTimeout",
    "setInterval", "clearTimeout", "clearInterval", "log", "warn", "error",
    "info", "debug", "toLowerCase", "toUpperCase", "toString", "trim", "slice",
    "splice", "concat", "indexOf", "includes", "forEach", "reduce", "find",
    "push", "shift", "unshift", "then", "catch", "finally", "stringify", "parse",
    "assign", "freeze", "entries", "fromEntries", "hasOwnProperty",
})


class Tier(str, Enum):
    LOCAL = "local"
    IMPORT = "import"
    HEURISTIC = "heuristic"
    BUILTIN = "builtin"
    UNRESOLVED = "unresolved"


_CONFIDENCE = {
    Tier.LOCAL: 1.0,
    Tier.IMPORT: 0.9,
    Tier.HEURISTIC: 0.5,
    Tier.BUILTIN: 0.0,
    Tier.UNRESOLVED: 0.0,
}


@dataclass(slots=True)
class ResolvedCall:
    caller: SymbolRef
    callee_name: str
    line: int
    tier: Tier
    callee: SymbolRef | None = None

    @property
    def confidence(self) -> float:
        return _CONFIDENCE[self.tier]


class CallResolver:
    """Resolves call sites against the whole parsed repo.

    `import_targets` maps a file path to the list of (ImportStmt, resolved
    target paths) produced by the import resolvers.
    """

    def __init__(
        self,
        parsed_files: dict[str, ParsedFile],
        import_targets: dict[str, list[tuple[object, list[str]]]],
    ) -> None:
        self._files = parsed_files
        self._import_targets = import_targets
        self._defs_by_file: dict[str, dict[str, list[SymbolDef]]] = {}
        self._global_defs: dict[str, list[SymbolRef]] = defaultdict(list)
        self._build_indexes()

    def _build_indexes(self) -> None:
        for path, parsed in self._files.items():
            by_name: dict[str, list[SymbolDef]] = defaultdict(list)
            for sym in parsed.symbols:
                by_name[sym.name].append(sym)
                self._global_defs[sym.name].append((path, sym.local_id))
            self._defs_by_file[path] = by_name

    # --- per-file lookup tables -----------------------------------------
    def _module_aliases(self, path: str) -> dict[str, str]:
        """Receiver name -> target file, for `import db` / `import * as ns`."""
        aliases: dict[str, str] = {}
        for stmt, targets in self._import_targets.get(path, []):
            if not targets:
                continue
            target = targets[0]
            alias = getattr(stmt, "alias", None)
            specifier = getattr(stmt, "raw_specifier", "") or ""
            if alias:
                aliases[alias] = target
                continue
            if specifier:
                aliases.setdefault(specifier, target)
                # `import a.b.c` is called as a.b.c.f(); also allow the tail.
                aliases.setdefault(specifier.split(".")[-1], target)
            # JS namespace/default imports arrive as imported_names.
            for name in getattr(stmt, "imported_names", []) or []:
                aliases.setdefault(name, target)
        return aliases

    def _imported_symbols(self, path: str) -> dict[str, list[str]]:
        """Bare name -> candidate target files, for `from config import x`."""
        table: dict[str, list[str]] = defaultdict(list)
        for stmt, targets in self._import_targets.get(path, []):
            if not targets:
                continue
            for name in getattr(stmt, "imported_names", []) or []:
                table[name].extend(targets)
            if getattr(stmt, "is_wildcard", False):
                # `from m import *` -- every public name in m is in scope.
                for target in targets:
                    target_file = self._files.get(target)
                    if target_file is None:
                        continue
                    for sym in target_file.symbols:
                        if sym.is_exported:
                            table[sym.name].append(target)
        return table

    # --- tiers -----------------------------------------------------------
    def _resolve_one(
        self,
        path: str,
        parsed: ParsedFile,
        call,
        aliases: dict[str, str],
        imported: dict[str, list[str]],
    ) -> tuple[Tier, SymbolRef | None]:
        name = call.callee_name
        receiver = call.receiver
        local_defs = self._defs_by_file.get(path, {})

        # Tier 1a: self.x() / this.x() -> method on the enclosing class.
        if receiver in _SELF_RECEIVERS and call.caller_local_id is not None:
            caller_sym = parsed.symbol_by_local_id(call.caller_local_id)
            class_id = caller_sym.parent_local_id if caller_sym is not None else None
            if class_id is not None:
                for cand in local_defs.get(name, []):
                    if cand.parent_local_id == class_id:
                        return Tier.LOCAL, (path, cand.local_id)

        # Tier 1b: bare foo() matching a top-level definition in this file.
        if receiver is None:
            top_level = [
                s for s in local_defs.get(name, [])
                if s.kind in (SymbolKind.FUNCTION, SymbolKind.CLASS)
                and s.parent_local_id is None
            ]
            if len(top_level) == 1:
                return Tier.LOCAL, (path, top_level[0].local_id)

        # Tier 2a: module-qualified call, db.connect() / ns.fmt().
        if receiver is not None and receiver in aliases:
            target = aliases[receiver]
            for cand in self._defs_by_file.get(target, {}).get(name, []):
                if cand.parent_local_id is None:
                    return Tier.IMPORT, (target, cand.local_id)

        # Tier 2b: directly imported name, `from config import get_setting`.
        if receiver is None:
            for target in imported.get(name, []):
                for cand in self._defs_by_file.get(target, {}).get(name, []):
                    if cand.parent_local_id is None:
                        return Tier.IMPORT, (target, cand.local_id)

        # Tier 3: unique repo-wide definition. Covers `user.save()` where the
        # receiver's type is unknown but only one `save` exists.
        candidates = self._global_defs.get(name, [])
        if len(candidates) == 1:
            return Tier.HEURISTIC, candidates[0]

        # Only classify as builtin once the repo has been ruled out, so a repo
        # symbol that happens to be named `get` still wins.
        if not candidates and name in _BUILTINS:
            return Tier.BUILTIN, None

        return Tier.UNRESOLVED, None

    def resolve_all(self) -> list[ResolvedCall]:
        resolved: list[ResolvedCall] = []
        for path, parsed in self._files.items():
            aliases = self._module_aliases(path)
            imported = self._imported_symbols(path)
            for call in parsed.calls:
                if call.caller_local_id is None:
                    # Module-level call: no caller symbol, so no graph edge.
                    continue
                tier, callee = self._resolve_one(path, parsed, call, aliases, imported)
                resolved.append(
                    ResolvedCall(
                        caller=(path, call.caller_local_id),
                        callee_name=call.callee_name,
                        line=call.line,
                        tier=tier,
                        callee=callee,
                    )
                )
        return resolved


def resolution_stats(calls: list[ResolvedCall]) -> dict[str, float | int]:
    """Tier breakdown, surfaced on the analysis so accuracy is visible."""
    total = len(calls)
    counts = {tier.value: 0 for tier in Tier}
    for call in calls:
        counts[call.tier.value] += 1
    builtin = counts[Tier.BUILTIN.value]
    in_repo = total - builtin
    resolved = in_repo - counts[Tier.UNRESOLVED.value]
    return {
        "total_calls": total,
        **{f"{k}_count": v for k, v in counts.items()},
        "in_repo_calls": in_repo,
        "resolved_count": resolved,
        # Measured over in-repo calls: builtins are excluded from both sides.
        "resolved_pct": round(100.0 * resolved / in_repo, 1) if in_repo else 0.0,
        # The heuristic tier is a unique-name guess at 0.5 confidence. Reporting
        # it separately keeps the headline number from over-claiming: on a real
        # repo the two figures differ a lot.
        "high_confidence_pct": round(
            100.0 * (counts[Tier.LOCAL.value] + counts[Tier.IMPORT.value]) / in_repo, 1
        ) if in_repo else 0.0,
    }
