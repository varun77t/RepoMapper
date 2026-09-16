"""Analysis endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AnalysisStatusResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    EntitiesResponse,
    FileDetailResponse,
    FileOut,
    GraphLevel,
    ImportOut,
    SymbolOut,
)
from app.db.models import Analysis, AnalysisStatus, CallEdge, ImportEdge, SourceFile, Symbol
from app.db.session import get_db
from app.graphing.serialization import graph_from_storage, graph_to_api
from app.ingestion.validation import InvalidRepoURL, parse_repo_url
from app.services import jobs
from app.services.analysis_service import create_analysis

router = APIRouter(tags=["analysis"])


def _get_analysis(db: Session, analysis_id: str) -> Analysis:
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found.")
    return analysis


def _require_complete(analysis: Analysis) -> None:
    if analysis.status == AnalysisStatus.FAILED.value:
        raise HTTPException(status_code=409, detail=f"Analysis failed: {analysis.error}")
    if analysis.status != AnalysisStatus.COMPLETE.value:
        raise HTTPException(
            status_code=409,
            detail=f"Analysis is not finished (status: {analysis.status}). Poll GET /analysis/{analysis.id}.",
        )


@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
def start_analysis(payload: AnalyzeRequest, db: Session = Depends(get_db)) -> AnalyzeResponse:
    """Queue a repository for analysis.

    Returns immediately with an id to poll. If this exact commit has already
    been analyzed, the existing result is returned without re-cloning.
    """
    try:
        ref = parse_repo_url(payload.repo_url)
    except InvalidRepoURL as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    analysis, cached = create_analysis(db, ref)
    if not cached:
        jobs.submit(analysis.id)

    return AnalyzeResponse(analysis_id=analysis.id, status=analysis.status, cached=cached)


@router.get("/analysis/{analysis_id}", response_model=AnalysisStatusResponse)
def get_analysis(analysis_id: str, db: Session = Depends(get_db)) -> AnalysisStatusResponse:
    analysis = _get_analysis(db, analysis_id)
    return AnalysisStatusResponse(
        analysis_id=analysis.id,
        repo_url=analysis.repo_url,
        repo_name=analysis.repo_name,
        commit_sha=analysis.commit_sha,
        default_branch=analysis.default_branch,
        status=analysis.status,
        progress_message=analysis.progress_message,
        error=analysis.error,
        file_count=analysis.file_count,
        symbol_count=analysis.symbol_count,
        call_resolution_stats=analysis.call_resolution_stats,
        skipped_files=analysis.skipped_files,
        created_at=analysis.created_at,
        completed_at=analysis.completed_at,
    )


@router.get("/analysis/{analysis_id}/entities", response_model=EntitiesResponse)
def get_entities(analysis_id: str, db: Session = Depends(get_db)) -> EntitiesResponse:
    """Raw extraction output, before any graph construction.

    This is the Phase 1 verification endpoint: it exists so extraction accuracy
    can be checked against the real source by eye.
    """
    analysis = _get_analysis(db, analysis_id)
    _require_complete(analysis)

    files = db.scalars(
        select(SourceFile).where(SourceFile.analysis_id == analysis_id).order_by(SourceFile.path)
    ).all()
    symbols_by_file: dict[str, list[Symbol]] = {}
    for symbol in db.scalars(select(Symbol).where(Symbol.analysis_id == analysis_id)).all():
        symbols_by_file.setdefault(symbol.file_id, []).append(symbol)
    imports_by_file: dict[str, list[ImportEdge]] = {}
    for edge in db.scalars(select(ImportEdge).where(ImportEdge.analysis_id == analysis_id)).all():
        imports_by_file.setdefault(edge.source_file_id, []).append(edge)

    path_by_id = {f.id: f.path for f in files}

    payload = []
    for file in files:
        payload.append(
            {
                "path": file.path,
                "language": file.language,
                "line_count": file.line_count,
                "is_entry_point": file.is_entry_point,
                "parse_error": file.parse_error,
                "symbols": [
                    {
                        "kind": s.kind,
                        "name": s.name,
                        "qualified_name": s.qualified_name,
                        "start_line": s.start_line,
                        "end_line": s.end_line,
                        "params": s.params,
                        "base_classes": s.base_classes,
                        "decorators": s.decorators,
                        "is_exported": s.is_exported,
                    }
                    for s in sorted(
                        symbols_by_file.get(file.id, []), key=lambda s: s.start_line
                    )
                ],
                "imports": [
                    {
                        "raw_specifier": e.raw_specifier,
                        "imported_names": e.imported_names,
                        "line": e.line,
                        "is_external": e.is_external,
                        "external_module": e.external_module,
                        "resolved_path": path_by_id.get(e.resolved_file_id),
                    }
                    for e in sorted(imports_by_file.get(file.id, []), key=lambda e: e.line)
                ],
            }
        )

    return EntitiesResponse(
        analysis_id=analysis_id, file_count=len(files), files=payload
    )


@router.get("/analysis/{analysis_id}/graph")
def get_graph(
    analysis_id: str,
    level: GraphLevel = Query("file", description="file | symbol | all"),
    db: Session = Depends(get_db),
) -> dict:
    """Graph in a flat, library-neutral {nodes, edges} shape."""
    analysis = _get_analysis(db, analysis_id)
    _require_complete(analysis)

    stored = analysis.graph_json or {}
    # The file view is precomputed at analysis time -- serve it directly.
    if level == "file" and "file_view" in stored:
        return stored["file_view"]

    if "storage" not in stored:
        raise HTTPException(status_code=409, detail="Graph data is unavailable for this analysis.")
    return graph_to_api(graph_from_storage(stored["storage"]), level=level)


@router.get("/analysis/{analysis_id}/insights")
def get_insights(analysis_id: str, db: Session = Depends(get_db)) -> dict:
    """Central files, communities, cycles, reading order and orphans."""
    analysis = _get_analysis(db, analysis_id)
    _require_complete(analysis)
    if not analysis.insights_json:
        raise HTTPException(status_code=409, detail="Insights are unavailable for this analysis.")
    return analysis.insights_json


@router.get("/analysis/{analysis_id}/files", response_model=list[FileOut])
def list_files(analysis_id: str, db: Session = Depends(get_db)) -> list[FileOut]:
    _require_complete(_get_analysis(db, analysis_id))
    files = db.scalars(
        select(SourceFile).where(SourceFile.analysis_id == analysis_id).order_by(SourceFile.path)
    ).all()
    return [FileOut.model_validate(f, from_attributes=True) for f in files]


@router.get("/analysis/{analysis_id}/files/{file_id}", response_model=FileDetailResponse)
def get_file_detail(
    analysis_id: str, file_id: str, db: Session = Depends(get_db)
) -> FileDetailResponse:
    """Everything known about one file: its symbols, what it imports, and what imports it."""
    _require_complete(_get_analysis(db, analysis_id))

    file = db.get(SourceFile, file_id)
    if file is None or file.analysis_id != analysis_id:
        raise HTTPException(status_code=404, detail=f"File '{file_id}' not found in this analysis.")

    symbols = db.scalars(
        select(Symbol).where(Symbol.file_id == file_id).order_by(Symbol.start_line)
    ).all()

    path_by_id = {
        f.id: f.path
        for f in db.scalars(
            select(SourceFile).where(SourceFile.analysis_id == analysis_id)
        ).all()
    }

    outgoing = db.scalars(
        select(ImportEdge)
        .where(ImportEdge.source_file_id == file_id)
        .order_by(ImportEdge.line)
    ).all()

    incoming = db.scalars(
        select(ImportEdge.source_file_id).where(ImportEdge.resolved_file_id == file_id)
    ).all()

    return FileDetailResponse(
        file=FileOut.model_validate(file, from_attributes=True),
        symbols=[SymbolOut.model_validate(s, from_attributes=True) for s in symbols],
        imports=[
            ImportOut(
                raw_specifier=e.raw_specifier,
                imported_names=e.imported_names,
                line=e.line,
                is_external=e.is_external,
                external_module=e.external_module,
                resolved_path=path_by_id.get(e.resolved_file_id),
            )
            for e in outgoing
        ],
        imported_by=sorted({path_by_id[i] for i in incoming if i in path_by_id}),
    )


@router.get("/analysis/{analysis_id}/calls")
def get_calls(
    analysis_id: str,
    resolution: str | None = Query(None, description="Filter by tier: local|import|heuristic|builtin|unresolved"),
    db: Session = Depends(get_db),
) -> dict:
    """Resolved call edges, with the tier that produced each one."""
    analysis = _get_analysis(db, analysis_id)
    _require_complete(analysis)

    query = select(CallEdge).where(CallEdge.analysis_id == analysis_id)
    if resolution:
        query = query.where(CallEdge.resolution == resolution)

    symbol_names = {
        s.id: (s.qualified_name, s.file_id)
        for s in db.scalars(select(Symbol).where(Symbol.analysis_id == analysis_id)).all()
    }
    path_by_id = {
        f.id: f.path
        for f in db.scalars(select(SourceFile).where(SourceFile.analysis_id == analysis_id)).all()
    }

    def describe(symbol_id: str | None) -> dict | None:
        if symbol_id is None or symbol_id not in symbol_names:
            return None
        name, file_id = symbol_names[symbol_id]
        return {"symbol_id": symbol_id, "qualified_name": name, "path": path_by_id.get(file_id)}

    edges = db.scalars(query).all()
    return {
        "stats": analysis.call_resolution_stats,
        "calls": [
            {
                "caller": describe(e.caller_symbol_id),
                "callee": describe(e.callee_symbol_id),
                "callee_name": e.callee_name_raw,
                "resolution": e.resolution,
                "confidence": e.confidence,
                "line": e.line,
            }
            for e in edges
        ],
    }
