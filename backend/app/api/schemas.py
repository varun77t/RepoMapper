"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    repo_url: str = Field(
        ...,
        description="Public GitHub repository URL, e.g. https://github.com/pallets/flask",
        examples=["https://github.com/pallets/flask"],
    )


class AnalyzeResponse(BaseModel):
    analysis_id: str
    status: str
    cached: bool = Field(description="True when a completed analysis for this commit existed.")


class AnalysisStatusResponse(BaseModel):
    analysis_id: str
    repo_url: str
    repo_name: str
    commit_sha: str | None
    default_branch: str | None
    status: str
    progress_message: str | None
    error: str | None
    file_count: int
    symbol_count: int
    call_resolution_stats: dict[str, Any] | None
    skipped_files: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


class SymbolOut(BaseModel):
    id: str
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    params: list[str] | None
    base_classes: list[str] | None
    decorators: list[str] | None
    is_exported: bool
    parent_symbol_id: str | None


class FileOut(BaseModel):
    id: str
    path: str
    language: str
    line_count: int
    size_bytes: int
    is_entry_point: bool
    parse_error: str | None
    summary: str | None


class ImportOut(BaseModel):
    raw_specifier: str
    imported_names: list[str] | None
    line: int
    is_external: bool
    external_module: str | None
    resolved_path: str | None


class FileDetailResponse(BaseModel):
    file: FileOut
    symbols: list[SymbolOut]
    imports: list[ImportOut]
    imported_by: list[str]


class EntitiesResponse(BaseModel):
    """Phase 1 verification endpoint: the raw extraction, before any graph work."""

    analysis_id: str
    file_count: int
    files: list[dict[str, Any]]


GraphLevel = Literal["file", "symbol", "all"]
