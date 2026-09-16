"""SQLAlchemy ORM models.

Design notes
------------
* A single ``symbols`` table holds functions, methods and classes. Methods nest
  under their class via the self-referencing ``parent_symbol_id`` -- this keeps
  joins simple versus three parallel tables.
* Normalized tables are the source of truth. ``graph_json`` / ``insights_json``
  on ``analyses`` are cached derived artifacts, recomputed if null.
* Portable ``JSON`` type (not PG ``JSONB``) so the unit tests can run on SQLite
  while production runs Postgres.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisStatus(str, enum.Enum):
    QUEUED = "queued"
    CLONING = "cloning"
    PARSING = "parsing"
    BUILDING_GRAPH = "building_graph"
    COMPUTING_INSIGHTS = "computing_insights"
    COMPLETE = "complete"
    FAILED = "failed"


class SymbolKind(str, enum.Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"


class CallResolution(str, enum.Enum):
    LOCAL = "local"
    IMPORT = "import"
    HEURISTIC = "heuristic"
    UNRESOLVED = "unresolved"


class AnalyzerVersion(Base):
    """Single row recording which analyzer version produced the cached results.

    A table rather than a column on `analyses` because the schema is created
    with `create_all`, which adds missing tables but never alters existing
    ones -- a new column would simply not appear on a database that already
    exists.
    """

    __tablename__ = "analyzer_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[str] = mapped_column(String(32), nullable=False)


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repo_url: Mapped[str] = mapped_column(String(500), index=True)
    repo_name: Mapped[str] = mapped_column(String(200))
    commit_sha: Mapped[str | None] = mapped_column(String(40), index=True)
    default_branch: Mapped[str | None] = mapped_column(String(200))

    status: Mapped[str] = mapped_column(String(32), default=AnalysisStatus.QUEUED.value, index=True)
    progress_message: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)

    file_count: Mapped[int] = mapped_column(Integer, default=0)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_files: Mapped[dict | None] = mapped_column(JSON)
    call_resolution_stats: Mapped[dict | None] = mapped_column(JSON)

    graph_json: Mapped[dict | None] = mapped_column(JSON)
    insights_json: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    files: Mapped[list["SourceFile"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )

    # Cache key: a repo at a given commit only needs analyzing once.
    __table_args__ = (UniqueConstraint("repo_url", "commit_sha", name="uq_analysis_repo_commit"),)


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1000))  # repo-relative, posix-normalized
    language: Mapped[str] = mapped_column(String(20))
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    is_entry_point: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)  # Phase 5 seam

    analysis: Mapped[Analysis] = relationship(back_populates="files")
    symbols: Mapped[list["Symbol"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_source_files_analysis_path", "analysis_id", "path", unique=True),)


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    parent_symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(300), index=True)
    qualified_name: Mapped[str] = mapped_column(String(600))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    params: Mapped[list | None] = mapped_column(JSON)
    base_classes: Mapped[list | None] = mapped_column(JSON)
    decorators: Mapped[list | None] = mapped_column(JSON)
    is_exported: Mapped[bool] = mapped_column(Boolean, default=False)

    file: Mapped[SourceFile] = relationship(back_populates="symbols")


class ImportEdge(Base):
    __tablename__ = "imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    source_file_id: Mapped[str] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    resolved_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    raw_specifier: Mapped[str] = mapped_column(String(500))
    imported_names: Mapped[list | None] = mapped_column(JSON)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    external_module: Mapped[str | None] = mapped_column(String(300))
    line: Mapped[int] = mapped_column(Integer, default=0)


class CallEdge(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    caller_symbol_id: Mapped[str] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True
    )
    callee_symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True
    )
    callee_name_raw: Mapped[str] = mapped_column(String(300))
    resolution: Mapped[str] = mapped_column(String(20), default=CallResolution.UNRESOLVED.value)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    line: Mapped[int] = mapped_column(Integer, default=0)
