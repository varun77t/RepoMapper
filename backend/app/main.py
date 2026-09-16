"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analyses
from app.config import settings
from app.db.models import Base
from app.db.session import engine
from app.db.session import SessionLocal
from app.services import jobs
from app.services.analysis_service import invalidate_stale_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_all rather than Alembic: the schema churns heavily through the
    # parsing/graph phases, and writing migrations against a moving schema is
    # wasted effort. Alembic is the follow-up once it settles. See README.
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        dropped = invalidate_stale_cache(session)
    if dropped:
        logger.warning(
            "Analyzer version changed; discarded %d cached analysis result(s)", dropped
        )

    stale = jobs.reset_stale_jobs()
    if stale:
        logger.warning("Marked %d interrupted analysis run(s) as failed", stale)

    yield
    jobs.shutdown_executor()


app = FastAPI(
    title="RepoMap",
    version="0.1.0",
    description=(
        "Static analysis and graph algorithms for understanding unfamiliar codebases. "
        "Clones a repo, parses it with tree-sitter, builds a dependency/call graph, and "
        "surfaces the files that matter and the order to read them in."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyses.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "llm_summaries_enabled": settings.enable_llm_summaries,
        "max_files": settings.max_files,
    }
