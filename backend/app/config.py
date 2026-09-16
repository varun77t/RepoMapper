"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    database_url: str = "postgresql+psycopg://repomap:repomap@localhost:5432/repomap"

    # --- Ingestion limits -------------------------------------------------
    # These are the blast-radius controls. Cloning an arbitrary user-supplied
    # URL is the least-trusted operation in the system.
    max_files: int = 5000
    max_file_bytes: int = 1_000_000
    clone_timeout_s: int = 120
    analysis_timeout_s: int = 600
    workspace_dir: str | None = None  # None -> system temp

    # --- Graph algorithm bounds ---
    top_central_files: int = 15
    cycle_length_bound: int = 8
    max_cycles_per_scc: int = 50

    # --- Jobs ---
    job_workers: int = 2

    # --- Frontend ---
    # Comma-separated rather than a list: pydantic-settings parses a list field
    # as JSON, which makes for an awkward thing to write in a .env file.
    # Keep this in step with the frontend's published port -- a mismatch fails
    # as an opaque CORS error in the browser with nothing in the server log.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Phase 5 (not implemented this pass) ---
    enable_llm_summaries: bool = False
    gemini_api_key: str | None = None

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, url: str) -> str:
        """Force the psycopg 3 driver onto a bare Postgres URL.

        Managed hosts hand out `postgres://` or `postgresql://`, and SQLAlchemy
        reads a bare `postgresql://` as "use psycopg2" -- which is not
        installed, because this project pins psycopg 3. The failure is an
        import error at engine construction, a long way from the environment
        variable that caused it, so the scheme is corrected on the way in.

        Left alone: sqlite, and any URL that already names its driver.
        """
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
