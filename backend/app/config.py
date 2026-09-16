"""Application settings, loaded from environment / .env."""
from __future__ import annotations

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

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
