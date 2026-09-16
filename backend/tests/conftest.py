from __future__ import annotations

from pathlib import Path

import pytest

from app.algorithms.insights import compute_insights
from app.graphing.builder import build_graph, file_subgraph
from app.services.analyzer import analyze_directory

FIXTURES = Path(__file__).parent / "fixtures"
PY_REPO = FIXTURES / "sample_repo"
TS_REPO = FIXTURES / "sample_repo_ts"


@pytest.fixture(scope="session")
def py_analysis():
    return analyze_directory(PY_REPO)


@pytest.fixture(scope="session")
def ts_analysis():
    return analyze_directory(TS_REPO)


@pytest.fixture(scope="session")
def py_graph(py_analysis):
    return build_graph(py_analysis)


@pytest.fixture(scope="session")
def ts_graph(ts_analysis):
    return build_graph(ts_analysis)


@pytest.fixture(scope="session")
def py_files(py_graph):
    return file_subgraph(py_graph)


@pytest.fixture(scope="session")
def ts_files(ts_graph):
    return file_subgraph(ts_graph)


@pytest.fixture(scope="session")
def py_insights(py_graph):
    return compute_insights(py_graph)


@pytest.fixture(scope="session")
def ts_insights(ts_graph):
    return compute_insights(ts_graph)


def paths_of(steps) -> list[str]:
    return [s["path"] for s in steps]
