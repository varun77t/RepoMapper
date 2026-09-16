"""Shallow-clone a repository into a disposable workspace.

Two details that are easy to get wrong:

* `git ls-remote` gives the HEAD commit SHA *without* cloning, so the analysis
  cache can be checked before doing any expensive work. A repeat request for an
  unchanged repo costs one network round trip instead of a full clone + parse.

* Cleanup fails on Windows. Git marks everything under .git/objects read-only,
  and shutil.rmtree cannot unlink a read-only file there -- it raises
  PermissionError. The onexc handler chmods the path writable and retries.

* Timeouts are platform-split. GitPython raises
  '"kill_after_timeout" feature is not supported on Windows' if that argument
  is passed there, so it is POSIX-only. Git's own low-speed abort is set on
  every platform and is the better guard anyway: it catches a stalled transfer,
  which is the failure mode that actually hangs a clone.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from git import GitCommandError, Repo

from app.ingestion.validation import RepoRef


class CloneError(RuntimeError):
    pass


@dataclass(slots=True)
class CloneResult:
    path: Path
    commit_sha: str
    default_branch: str | None


def _timeout_kwargs(timeout_s: int) -> dict:
    """kill_after_timeout is POSIX-only; passing it on Windows raises."""
    if sys.platform.startswith("win"):
        return {}
    return {"kill_after_timeout": timeout_s}


def _low_speed_env(timeout_s: int) -> dict[str, str]:
    """Abort a transfer that drops below 1 KB/s for the whole timeout window."""
    return {
        "GIT_HTTP_LOW_SPEED_LIMIT": "1000",
        "GIT_HTTP_LOW_SPEED_TIME": str(timeout_s),
        "GIT_TERMINAL_PROMPT": "0",  # never block waiting for credentials
    }


def _force_remove_readonly(func, path, _exc):
    """Make a read-only path writable and retry -- required for .git on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove_readonly)
    else:  # pragma: no cover - the project targets 3.12+
        shutil.rmtree(path, onerror=lambda f, p, e: _force_remove_readonly(f, p, e))


def get_head_sha(ref: RepoRef, timeout_s: int = 30) -> str | None:
    """HEAD commit SHA without cloning. Returns None if the repo is unreachable."""
    from git.cmd import Git

    git = Git()
    git.update_environment(**_low_speed_env(timeout_s))
    try:
        raw = git.ls_remote(ref.clone_url, "HEAD", **_timeout_kwargs(timeout_s))
    except (GitCommandError, OSError):
        return None
    if not raw:
        return None
    first = raw.strip().split("\n")[0]
    sha = first.split()[0] if first else ""
    return sha or None


def _git_error(exc: GitCommandError) -> str:
    """Pull the useful line out of a GitCommandError.

    .stderr is often empty (the message is in .stdout or the repr), so fall
    back rather than reporting a bare command line to the user.
    """
    for candidate in (exc.stderr, exc.stdout, str(exc)):
        text = (candidate or "").strip()
        lines = [
            line.strip().removeprefix("stderr:").strip().strip("'")
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("cmdline:")
        ]
        if lines:
            return lines[-1]
    return "unknown git error"


@contextmanager
def clone_repo(ref: RepoRef, timeout_s: int = 120, workspace_dir: str | None = None):
    """Clone into a temp directory, yield a CloneResult, always clean up."""
    workspace = Path(mkdtemp(prefix="repomap-", dir=workspace_dir))
    target = workspace / ref.name
    try:
        try:
            repo = Repo.clone_from(
                ref.clone_url,
                target,
                depth=1,
                single_branch=True,
                no_checkout=False,
                env=_low_speed_env(timeout_s),
                **_timeout_kwargs(timeout_s),
            )
        except GitCommandError as exc:
            raise CloneError(f"Could not clone {ref.full_name}: {_git_error(exc)}") from exc

        try:
            commit_sha = repo.head.commit.hexsha
        except Exception as exc:  # empty repository
            raise CloneError(f"{ref.full_name} has no commits to analyze.") from exc

        try:
            default_branch = repo.active_branch.name
        except TypeError:  # detached HEAD
            default_branch = None

        repo.close()
        yield CloneResult(path=target, commit_sha=commit_sha, default_branch=default_branch)
    finally:
        remove_tree(workspace)
