"""Pipeline orchestration and persistence.

Drives clone -> parse -> resolve -> graph -> insights -> store, writing status
transitions to the database as it goes so a polling client can show progress.

This is the only module that knows about both the analysis core and the ORM.
Everything below it (parsing, graphing, algorithms) stays database-free.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.algorithms.centrality import compute_pagerank
from app.algorithms.insights import compute_insights
from app.config import settings
from app.db.models import (
    Analysis,
    AnalyzerVersion,
    AnalysisStatus,
    CallEdge,
    ImportEdge,
    SourceFile,
    Symbol,
)
from app.graphing.builder import build_graph, file_subgraph
from app.graphing.serialization import graph_to_api, graph_to_storage
from app.ingestion.cloner import CloneError, clone_repo, get_head_sha
from app.ingestion.validation import InvalidRepoURL, RepoRef, parse_repo_url
from app.services.analyzer import RepoAnalysis, analyze_directory
from app.version import ANALYZER_VERSION

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def set_status(db: Session, analysis: Analysis, status: AnalysisStatus, message: str) -> None:
    analysis.status = status.value
    analysis.progress_message = message
    db.commit()


def invalidate_stale_cache(db: Session) -> int:
    """Drop cached results produced by an older analyzer, once, at startup.

    Everything in these tables is derived from the repository, so discarding
    them costs a re-run and nothing else. Serving a result the current code
    would no longer produce costs the user's trust in the output.
    """
    record = db.get(AnalyzerVersion, 1)
    if record is not None and record.version == ANALYZER_VERSION:
        return 0

    dropped = db.query(Analysis).delete()
    if record is None:
        db.add(AnalyzerVersion(id=1, version=ANALYZER_VERSION))
    else:
        record.version = ANALYZER_VERSION
    db.commit()
    return dropped


def find_cached(db: Session, repo_url: str, commit_sha: str | None) -> Analysis | None:
    """A repo at a given commit only needs analyzing once."""
    if not commit_sha:
        return None
    return db.scalar(
        select(Analysis).where(
            Analysis.repo_url == repo_url,
            Analysis.commit_sha == commit_sha,
            Analysis.status == AnalysisStatus.COMPLETE.value,
        )
    )


def create_analysis(db: Session, ref: RepoRef) -> tuple[Analysis, bool]:
    """Return (analysis, was_cached).

    The HEAD SHA is fetched with ls-remote first, so a cache hit costs one
    network round trip instead of a clone and a full parse.
    """
    head_sha = get_head_sha(ref, timeout_s=min(settings.clone_timeout_s, 30))
    cached = find_cached(db, ref.canonical_url, head_sha)
    if cached is not None:
        return cached, True

    analysis = Analysis(
        repo_url=ref.canonical_url,
        repo_name=ref.full_name,
        commit_sha=None,
        status=AnalysisStatus.QUEUED.value,
        progress_message="Queued",
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis, False


def _persist(
    db: Session,
    analysis: Analysis,
    result: RepoAnalysis,
    graph,
    insights: dict,
) -> None:
    entry_paths = {e["path"] for e in insights["entry_points"]}

    file_rows: dict[str, SourceFile] = {}
    for path, parsed in result.files.items():
        row = SourceFile(
            analysis_id=analysis.id,
            path=path,
            language=parsed.language.value if parsed.language else "unknown",
            line_count=parsed.line_count,
            size_bytes=parsed.size_bytes,
            is_entry_point=path in entry_paths,
            parse_error=parsed.parse_error,
        )
        db.add(row)
        file_rows[path] = row
    db.flush()

    # (file path, local_id) -> database id, needed to wire up call edges.
    symbol_ids: dict[tuple[str, int], str] = {}
    symbol_rows: dict[tuple[str, int], Symbol] = {}
    for path, parsed in result.files.items():
        for sym in parsed.symbols:
            row = Symbol(
                analysis_id=analysis.id,
                file_id=file_rows[path].id,
                kind=sym.kind.value,
                name=sym.name,
                qualified_name=sym.qualified_name,
                start_line=sym.start_line,
                end_line=sym.end_line,
                params=list(sym.params),
                base_classes=list(sym.base_classes),
                decorators=list(sym.decorators),
                is_exported=sym.is_exported,
            )
            db.add(row)
            symbol_rows[(path, sym.local_id)] = row
    db.flush()
    for key, row in symbol_rows.items():
        symbol_ids[key] = row.id

    # Parent links need the ids, so this is a second pass.
    for path, parsed in result.files.items():
        for sym in parsed.symbols:
            if sym.parent_local_id is None:
                continue
            parent = symbol_ids.get((path, sym.parent_local_id))
            if parent:
                symbol_rows[(path, sym.local_id)].parent_symbol_id = parent

    for imp in result.imports:
        db.add(
            ImportEdge(
                analysis_id=analysis.id,
                source_file_id=file_rows[imp.source_path].id,
                resolved_file_id=(
                    file_rows[imp.target_path].id if imp.target_path in file_rows else None
                ),
                raw_specifier=imp.raw_specifier[:500],
                imported_names=list(imp.imported_names),
                is_external=imp.is_external,
                external_module=imp.external_module,
                line=imp.line,
            )
        )

    for call in result.calls:
        caller_id = symbol_ids.get(call.caller)
        if caller_id is None:
            continue
        db.add(
            CallEdge(
                analysis_id=analysis.id,
                caller_symbol_id=caller_id,
                callee_symbol_id=symbol_ids.get(call.callee) if call.callee else None,
                callee_name_raw=call.callee_name[:300],
                resolution=call.tier.value,
                confidence=call.confidence,
                line=call.line,
            )
        )

    files = file_subgraph(graph)
    pagerank = compute_pagerank(files)

    analysis.file_count = len(result.files)
    analysis.symbol_count = sum(len(p.symbols) for p in result.files.values())
    analysis.call_resolution_stats = result.call_stats
    analysis.skipped_files = result.skipped
    analysis.graph_json = {
        "storage": graph_to_storage(graph),
        "file_view": graph_to_api(graph, "file", pagerank, insights["communities"]),
    }
    analysis.insights_json = insights
    db.commit()


def run_analysis(db: Session, analysis_id: str) -> None:
    """Execute the full pipeline for a queued analysis.

    Never raises: failures are recorded on the row so the polling client sees a
    terminal state rather than a request that silently never completes.
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        logger.warning("run_analysis: no such analysis %s", analysis_id)
        return

    try:
        ref = parse_repo_url(analysis.repo_url)

        set_status(db, analysis, AnalysisStatus.CLONING, f"Cloning {ref.full_name}")
        with clone_repo(
            ref, timeout_s=settings.clone_timeout_s, workspace_dir=settings.workspace_dir
        ) as clone:
            analysis.commit_sha = clone.commit_sha
            analysis.default_branch = clone.default_branch
            set_status(db, analysis, AnalysisStatus.PARSING, "Parsing source files")

            result = analyze_directory(
                clone.path,
                max_files=settings.max_files,
                max_file_bytes=settings.max_file_bytes,
            )

        if not result.files:
            raise ValueError("No supported source files (.py/.js/.jsx/.ts/.tsx) were found.")

        set_status(db, analysis, AnalysisStatus.BUILDING_GRAPH, "Building dependency graph")
        graph = build_graph(result)

        set_status(db, analysis, AnalysisStatus.COMPUTING_INSIGHTS, "Running graph algorithms")
        insights = compute_insights(
            graph,
            top_n=settings.top_central_files,
            cycle_length_bound=settings.cycle_length_bound,
            max_cycles_per_scc=settings.max_cycles_per_scc,
        )

        _persist(db, analysis, result, graph, insights)

        analysis.completed_at = _now()
        set_status(
            db, analysis, AnalysisStatus.COMPLETE,
            f"Analyzed {analysis.file_count} files",
        )

    except (InvalidRepoURL, CloneError, ValueError) as exc:
        db.rollback()
        _fail(db, analysis_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - a worker must not die silently
        logger.exception("Analysis %s failed", analysis_id)
        db.rollback()
        _fail(db, analysis_id, f"{type(exc).__name__}: {exc}")


def _fail(db: Session, analysis_id: str, message: str) -> None:
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        return
    analysis.status = AnalysisStatus.FAILED.value
    analysis.progress_message = "Failed"
    analysis.error = message[:2000]
    analysis.completed_at = _now()
    db.commit()
