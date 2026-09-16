"""Architecture boundaries.

The parsing -> graphing -> algorithms layering is the main design claim of this
codebase, so it is asserted rather than merely documented. Each check runs in a
subprocess: importing anything in the test process first would pollute
sys.modules and make the assertion meaningless.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

FORBIDDEN = ("sqlalchemy", "fastapi", "psycopg", "git", "uvicorn")


def _import_and_report(module: str) -> set[str]:
    script = textwrap.dedent(
        f"""
        import sys
        import {module}  # noqa: F401
        print(",".join(sorted(m.split(".")[0] for m in sys.modules)))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.strip().split(","))


def test_algorithms_layer_has_no_framework_dependencies():
    """The five algorithms must be pure graph code: no ORM, no web framework.

    This is what makes them unit-testable against synthetic graphs with no
    database or HTTP server involved.
    """
    loaded = _import_and_report("app.algorithms.insights")
    assert not (loaded & set(FORBIDDEN)), (
        f"algorithms layer pulled in {sorted(loaded & set(FORBIDDEN))}"
    )


def test_parsing_layer_has_no_framework_dependencies():
    loaded = _import_and_report("app.parsing.python_extractor")
    assert not (loaded & set(FORBIDDEN))


def test_graphing_layer_has_no_framework_dependencies():
    loaded = _import_and_report("app.graphing.builder")
    assert not (loaded & set(FORBIDDEN))


def test_analyzer_needs_no_database_or_network():
    """analyze_directory() takes a path and returns dataclasses."""
    loaded = _import_and_report("app.services.analyzer")
    assert not (loaded & {"sqlalchemy", "fastapi", "psycopg"})
