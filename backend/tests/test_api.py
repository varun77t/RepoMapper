"""End-to-end API tests.

Runs the real pipeline (parse -> graph -> algorithms -> persist -> serve) against
the fixture repo on an in-memory SQLite database. Only two things are stubbed:
the git clone, which is replaced by a pointer at the fixture directory, and the
background executor, which runs inline so assertions are deterministic.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.ingestion.cloner import CloneResult
from app.main import app
from app.services import analysis_service, jobs
from tests.conftest import PY_REPO, TS_REPO

FAKE_SHA = "a" * 40


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient wired to SQLite, cloning a local fixture directory.

    TestClient is intentionally not used as a context manager: that would run
    the lifespan handler, which calls create_all against the real Postgres URL.
    """

    def _make(repo_path=PY_REPO, sha=FAKE_SHA, fail_with=None):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        @contextmanager
        def fake_clone(ref, timeout_s=120, workspace_dir=None):
            if fail_with is not None:
                raise fail_with
            yield CloneResult(path=repo_path, commit_sha=sha, default_branch="main")

        monkeypatch.setattr(analysis_service, "clone_repo", fake_clone)
        monkeypatch.setattr(analysis_service, "get_head_sha", lambda ref, timeout_s=30: sha)

        # Run inline rather than on the thread pool so tests are deterministic.
        def run_inline(analysis_id: str) -> None:
            db = TestSession()
            try:
                analysis_service.run_analysis(db, analysis_id)
            finally:
                db.close()

        monkeypatch.setattr(jobs, "submit", run_inline)

        def override_get_db():
            db = TestSession()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        client.session_factory = TestSession
        return client

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client):
    return make_client()


def analyze(client, url="https://github.com/example/sample") -> str:
    response = client.post("/analyze", json={"repo_url": url})
    assert response.status_code == 202, response.text
    return response.json()["analysis_id"]


class TestHealth:
    def test_health(self, client):
        assert client.get("/health").json()["status"] == "ok"


class TestStaleCacheInvalidation:
    """Results are cached per commit, which is only safe while the analyzer is
    fixed. When resolution improves, a previously-analyzed repo must not keep
    serving the worse answer."""

    @staticmethod
    def _session():
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    def _seed(self, db):
        from app.db.models import Analysis

        db.add(Analysis(repo_url="https://github.com/a/b", repo_name="a/b", status="complete"))
        db.commit()

    def test_first_run_records_the_version_and_keeps_nothing_stale(self):
        from app.db.models import AnalyzerVersion
        from app.services.analysis_service import invalidate_stale_cache
        from app.version import ANALYZER_VERSION

        db = self._session()
        assert invalidate_stale_cache(db) == 0
        assert db.get(AnalyzerVersion, 1).version == ANALYZER_VERSION

    def test_matching_version_keeps_the_cache(self):
        from app.db.models import Analysis
        from app.services.analysis_service import invalidate_stale_cache

        db = self._session()
        invalidate_stale_cache(db)
        self._seed(db)
        assert invalidate_stale_cache(db) == 0
        assert db.query(Analysis).count() == 1

    def test_changed_version_discards_the_cache(self):
        from app.db.models import Analysis, AnalyzerVersion
        from app.services.analysis_service import invalidate_stale_cache
        from app.version import ANALYZER_VERSION

        db = self._session()
        db.add(AnalyzerVersion(id=1, version="0"))
        db.commit()
        self._seed(db)

        assert invalidate_stale_cache(db) == 1
        assert db.query(Analysis).count() == 0
        assert db.get(AnalyzerVersion, 1).version == ANALYZER_VERSION


class TestCorsSettings:
    """The frontend's origin is configuration, not a constant.

    A mismatch here surfaces only as an opaque browser CORS error with nothing
    in the server log, so the parsing is worth pinning down.
    """

    def test_default_origins_cover_dev_server_and_container(self):
        from app.config import Settings

        assert Settings().cors_origin_list == [
            "http://localhost:5173",
            "http://localhost:3000",
        ]

    def test_whitespace_and_empty_entries_are_ignored(self):
        from app.config import Settings

        settings = Settings(cors_origins=" https://a.example , , https://b.example ")
        assert settings.cors_origin_list == ["https://a.example", "https://b.example"]


class TestDatabaseUrlDriver:
    """Managed Postgres hands out a URL with no driver in it.

    SQLAlchemy reads a bare `postgresql://` as psycopg2, which this project
    does not install, so the deploy dies at import with a ModuleNotFoundError
    naming a package nobody asked for.
    """

    def test_bare_postgres_scheme_gets_psycopg3(self):
        from app.config import Settings

        settings = Settings(database_url="postgres://u:p@host:5432/db")
        assert settings.database_url == "postgresql+psycopg://u:p@host:5432/db"

    def test_bare_postgresql_scheme_gets_psycopg3(self):
        from app.config import Settings

        settings = Settings(database_url="postgresql://u:p@host:5432/db")
        assert settings.database_url == "postgresql+psycopg://u:p@host:5432/db"

    def test_an_explicit_driver_is_left_alone(self):
        from app.config import Settings

        url = "postgresql+psycopg://u:p@host:5432/db"
        assert Settings(database_url=url).database_url == url

    def test_sqlite_is_left_alone(self):
        from app.config import Settings

        assert Settings(database_url="sqlite:///./x.db").database_url == "sqlite:///./x.db"


class TestAnalyzeEndpoint:
    def test_returns_202_with_an_id(self, client):
        response = client.post("/analyze", json={"repo_url": "https://github.com/example/sample"})
        assert response.status_code == 202
        body = response.json()
        assert body["analysis_id"]
        assert body["cached"] is False

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "https://gitlab.com/a/b", "ssh://git@github.com/a/b", "not a url"],
    )
    def test_rejects_unsafe_urls_with_400(self, client, url):
        response = client.post("/analyze", json={"repo_url": url})
        assert response.status_code == 400

    def test_second_request_for_same_commit_is_cached(self, client):
        first = analyze(client)
        response = client.post("/analyze", json={"repo_url": "https://github.com/example/sample"})
        assert response.json()["cached"] is True
        assert response.json()["analysis_id"] == first

    def test_status_reaches_complete(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}").json()
        assert body["status"] == "complete"
        assert body["file_count"] == 9
        assert body["commit_sha"] == FAKE_SHA
        assert body["call_resolution_stats"]["resolved_pct"] == 100.0

    def test_clone_failure_is_recorded_not_raised(self, make_client):
        from app.ingestion.cloner import CloneError

        client = make_client(fail_with=CloneError("repository not found"))
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}").json()
        assert body["status"] == "failed"
        assert "repository not found" in body["error"]

    def test_unknown_analysis_returns_404(self, client):
        assert client.get("/analysis/does-not-exist").status_code == 404


class TestEntitiesEndpoint:
    def test_returns_parsed_structure_for_every_file(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/entities").json()
        assert body["file_count"] == 9

        db_py = next(f for f in body["files"] if f["path"] == "db.py")
        assert {s["name"] for s in db_py["symbols"]} == {
            "Connection", "__init__", "execute", "parse", "connect"
        }
        assert db_py["imports"][0]["resolved_path"] == "config.py"

    def test_entry_point_is_flagged(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/entities").json()
        assert next(f for f in body["files"] if f["path"] == "main.py")["is_entry_point"]


class TestGraphEndpoint:
    def test_file_level_graph(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/graph").json()
        assert body["level"] == "file"
        assert len(body["nodes"]) == 9
        assert len(body["edges"]) == 11
        assert all(n["type"] == "file" for n in body["nodes"])

    def test_file_nodes_carry_rendering_attributes(self, client):
        """Centrality sizes the node and community colours it in the frontend."""
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/graph").json()
        config = next(n for n in body["nodes"] if n["path"] == "config.py")
        assert config["pagerank"] > 0
        assert config["community"] is not None
        assert config["in_degree"] == 4
        assert config["out_degree"] == 0

    def test_symbol_level_graph(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/graph?level=symbol").json()
        assert body["level"] == "symbol"
        assert all(n["type"] in ("function", "method", "class") for n in body["nodes"])
        assert all(e["type"] in ("calls", "inherits_from") for e in body["edges"])

    def test_call_edges_expose_their_resolution_tier(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/graph?level=symbol").json()
        calls = [e for e in body["edges"] if e["type"] == "calls"]
        assert calls
        assert all(e["resolution"] in ("local", "import", "heuristic") for e in calls)

    def test_inheritance_edge_is_present(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/graph?level=symbol").json()
        assert any(e["type"] == "inherits_from" for e in body["edges"])

    def test_invalid_level_is_rejected(self, client):
        analysis_id = analyze(client)
        assert client.get(f"/analysis/{analysis_id}/graph?level=bogus").status_code == 422


class TestInsightsEndpoint:
    def test_all_five_algorithms_are_present(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/insights").json()
        assert body["central_files"][0]["path"] == "config.py"
        assert body["communities"]
        assert set(body["cycles"]["cycles"][0]["paths"]) == {"cycle_a.py", "cycle_b.py"}
        assert body["reading_order"][0]["path"] == "main.py"
        assert [f["path"] for f in body["orphans"]["files"]] == ["orphan.py"]

    def test_summary_counts(self, client):
        analysis_id = analyze(client)
        summary = client.get(f"/analysis/{analysis_id}/insights").json()["summary"]
        assert summary["file_count"] == 9
        assert summary["import_edge_count"] == 11
        assert summary["cycle_count"] == 1

    def test_typescript_repo_insights(self, make_client):
        client = make_client(repo_path=TS_REPO, sha="b" * 40)
        analysis_id = analyze(client, "https://github.com/example/ts-sample")
        body = client.get(f"/analysis/{analysis_id}/insights").json()
        assert body["central_files"][0]["path"] == "src/lib/format.ts"
        assert body["cycles"]["has_cycles"] is False
        assert [f["path"] for f in body["orphans"]["files"]] == ["src/orphan.ts"]


class TestFileEndpoints:
    def test_list_files(self, client):
        analysis_id = analyze(client)
        files = client.get(f"/analysis/{analysis_id}/files").json()
        assert len(files) == 9
        assert {f["path"] for f in files} >= {"main.py", "config.py"}

    def test_file_detail_shows_both_import_directions(self, client):
        analysis_id = analyze(client)
        files = client.get(f"/analysis/{analysis_id}/files").json()
        config_id = next(f["id"] for f in files if f["path"] == "config.py")

        detail = client.get(f"/analysis/{analysis_id}/files/{config_id}").json()
        assert detail["file"]["path"] == "config.py"
        assert detail["imports"] == []
        assert sorted(detail["imported_by"]) == [
            "db.py", "models.py", "orphan.py", "utils.py"
        ]
        assert {s["name"] for s in detail["symbols"]} == {
            "get_setting", "Settings", "__init__", "as_dict"
        }

    def test_methods_are_linked_to_their_class(self, client):
        analysis_id = analyze(client)
        files = client.get(f"/analysis/{analysis_id}/files").json()
        db_id = next(f["id"] for f in files if f["path"] == "db.py")
        detail = client.get(f"/analysis/{analysis_id}/files/{db_id}").json()

        klass = next(s for s in detail["symbols"] if s["kind"] == "class")
        methods = [s for s in detail["symbols"] if s["kind"] == "method"]
        assert methods and all(m["parent_symbol_id"] == klass["id"] for m in methods)

    def test_file_from_another_analysis_is_404(self, client):
        analysis_id = analyze(client)
        assert client.get(f"/analysis/{analysis_id}/files/nope").status_code == 404


class TestCallsEndpoint:
    def test_lists_calls_with_tiers(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/calls").json()
        assert body["stats"]["resolved_pct"] == 100.0
        assert body["calls"]

    def test_filter_by_resolution_tier(self, client):
        analysis_id = analyze(client)
        body = client.get(f"/analysis/{analysis_id}/calls?resolution=local").json()
        assert body["calls"]
        assert all(c["resolution"] == "local" for c in body["calls"])
        assert all(c["confidence"] == 1.0 for c in body["calls"])


class TestIncompleteAnalysisGuards:
    def test_graph_requires_a_complete_analysis(self, make_client):
        from app.ingestion.cloner import CloneError

        client = make_client(fail_with=CloneError("boom"))
        analysis_id = analyze(client)
        for suffix in ("graph", "insights", "entities"):
            response = client.get(f"/analysis/{analysis_id}/{suffix}")
            assert response.status_code == 409
            assert "failed" in response.json()["detail"].lower()
