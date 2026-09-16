"""URL validation -- the untrusted-input boundary -- and the file walker."""
from __future__ import annotations

import pytest

from app.ingestion.validation import InvalidRepoURL, parse_repo_url
from app.ingestion.walker import walk_repo
from tests.conftest import PY_REPO, TS_REPO


class TestRepoUrlValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/pallets/flask",
            "https://github.com/pallets/flask.git",
            "https://www.github.com/pallets/flask",
            "http://github.com/pallets/flask",
            "https://github.com/pallets/flask/",
            "  https://github.com/pallets/flask  ",
            "pallets/flask",
        ],
    )
    def test_accepts_valid_github_urls(self, url):
        ref = parse_repo_url(url)
        assert ref.owner == "pallets"
        assert ref.name == "flask"
        assert ref.clone_url == "https://github.com/pallets/flask.git"

    @pytest.mark.parametrize(
        "url,reason",
        [
            ("file:///etc/passwd", "local file transport"),
            ("file://C:/Windows/System32", "local file transport"),
            ("ssh://git@github.com/a/b", "non-http transport"),
            ("git://github.com/a/b", "git transport"),
            ("https://gitlab.com/a/b", "host not allowlisted"),
            ("https://169.254.169.254/a/b", "cloud metadata SSRF"),
            ("https://localhost:8000/a/b", "internal host"),
            ("https://github.com/a/b/c", "not owner/repo"),
            ("https://github.com/onlyowner", "missing repo"),
            ("--upload-pack=touch /tmp/pwned", "argument injection"),
            ("https://user:pass@github.com/a/b", "embedded credentials"),
            ("https://github.com/../../etc/a", "path traversal"),
            ("", "empty"),
        ],
    )
    def test_rejects_unsafe_urls(self, url, reason):
        with pytest.raises(InvalidRepoURL):
            parse_repo_url(url)

    def test_canonical_url_strips_git_suffix(self):
        ref = parse_repo_url("https://github.com/pallets/flask.git")
        assert ref.canonical_url == "https://github.com/pallets/flask"
        assert ref.full_name == "pallets/flask"


class TestWalker:
    def test_finds_all_python_fixture_files(self):
        result = walk_repo(PY_REPO, max_files=5000, max_file_bytes=1_000_000)
        assert len(result.files) == 9
        assert result.skipped == {}

    def test_finds_ts_files_and_reads_tsconfig(self):
        result = walk_repo(TS_REPO, max_files=5000, max_file_bytes=1_000_000)
        assert len(result.files) == 6
        assert "tsconfig.json" in result.configs

    def test_paths_are_posix_relative(self):
        result = walk_repo(TS_REPO, max_files=5000, max_file_bytes=1_000_000)
        paths = {f.rel_path for f in result.files}
        assert "src/lib/format.ts" in paths
        assert all("\\" not in p for p in paths)

    def test_file_limit_is_enforced_and_reported(self):
        result = walk_repo(PY_REPO, max_files=3, max_file_bytes=1_000_000)
        assert len(result.files) == 3
        assert result.truncated is True
        assert result.skipped["file_limit"] == 6

    def test_oversized_files_are_skipped(self):
        """Only files at or under the byte limit survive."""
        limit = 100
        result = walk_repo(PY_REPO, max_files=5000, max_file_bytes=limit)
        assert result.files, "fixture should have at least one small file"
        assert all(f.size_bytes <= limit for f in result.files)
        assert result.skipped["too_large"] == 9 - len(result.files)

    def test_minified_bundles_are_skipped(self, tmp_path):
        (tmp_path / "vendor.min.js").write_text("var a=1;" * 10)
        (tmp_path / "real.js").write_text("function a() {\n  return 1;\n}\n")
        result = walk_repo(tmp_path, max_files=5000, max_file_bytes=1_000_000)
        assert [f.rel_path for f in result.files] == ["real.js"]
        assert result.skipped["minified"] == 1

    def test_ignored_directories_are_pruned(self, tmp_path):
        (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "node_modules" / "pkg" / "index.js").write_text("module.exports={};\n")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "x.py").write_text("x = 1\n")
        (tmp_path / "app.py").write_text("x = 1\n")
        result = walk_repo(tmp_path, max_files=5000, max_file_bytes=1_000_000)
        assert [f.rel_path for f in result.files] == ["app.py"]
