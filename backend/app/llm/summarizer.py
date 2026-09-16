"""Optional plain-English file summaries (Phase 5 seam).

Deliberately isolated. Nothing in parsing/, graphing/ or algorithms/ imports
this module, and the application is fully functional with it disabled -- the
core value is the static analysis, not the model.

Only the Protocol and a no-op implementation exist at this stage. The intended
provider is Gemini (google-genai), called on demand for a single file rather
than across the whole repo upfront, so cost scales with what a user actually
opens.
"""
from __future__ import annotations

from typing import Protocol

from app.config import settings


class Summarizer(Protocol):
    def summarize_file(self, path: str, source: str, structure: dict) -> str | None:
        """One or two sentences describing what a file does, or None."""


class NullSummarizer:
    """Used whenever summaries are disabled. Keeps callers branch-free."""

    enabled = False

    def summarize_file(self, path: str, source: str, structure: dict) -> str | None:
        return None


def get_summarizer() -> Summarizer:
    if not settings.enable_llm_summaries or not settings.gemini_api_key:
        return NullSummarizer()
    raise NotImplementedError(
        "LLM summaries are enabled but no provider is wired up yet (Phase 5)."
    )
