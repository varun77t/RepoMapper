"""Background execution for analysis runs.

POST /analyze must not block: cloning and parsing take seconds to minutes. The
endpoint enqueues here and returns an id the client polls.

A ThreadPoolExecutor is the right size of tool for this. The work is a long
blocking sequence of subprocess (git) and CPU (tree-sitter, networkx) calls, so
running it in a worker thread keeps the event loop responsive.

KNOWN LIMITATIONS -- documented rather than hidden:
  * In-process. Jobs are lost if the server restarts mid-run; any analysis left
    in a non-terminal state on boot is marked failed by reset_stale_jobs().
  * Assumes a single web worker. With multiple uvicorn workers each process
    gets its own pool, so a job is invisible to the process that did not run it.
  * No retries and no priority.
Moving to Celery / RQ / arq with Redis is the upgrade path; it is not warranted
until the deployment actually runs more than one worker.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update

from app.config import settings
from app.db.models import Analysis, AnalysisStatus
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None

_TERMINAL = (AnalysisStatus.COMPLETE.value, AnalysisStatus.FAILED.value)


def get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.job_workers, thread_name_prefix="repomap-job"
        )
    return _executor


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def _run(analysis_id: str) -> None:
    """Thread entry point.

    Creates its own Session: SQLAlchemy sessions are not thread-safe, so the
    request's session must never cross into a worker.
    """
    from app.services.analysis_service import run_analysis

    db = SessionLocal()
    try:
        run_analysis(db, analysis_id)
    except Exception:  # noqa: BLE001 - a dead worker must still be logged
        logger.exception("Unhandled error in job %s", analysis_id)
    finally:
        db.close()


def submit(analysis_id: str) -> None:
    get_executor().submit(_run, analysis_id)


def reset_stale_jobs() -> int:
    """Fail analyses left mid-flight by a restart, so clients stop polling."""
    db = SessionLocal()
    try:
        stale = db.scalars(
            select(Analysis.id).where(Analysis.status.not_in(_TERMINAL))
        ).all()
        if not stale:
            return 0
        db.execute(
            update(Analysis)
            .where(Analysis.id.in_(stale))
            .values(
                status=AnalysisStatus.FAILED.value,
                progress_message="Failed",
                error="Interrupted by a server restart. Re-run the analysis.",
            )
        )
        db.commit()
        return len(stale)
    finally:
        db.close()
