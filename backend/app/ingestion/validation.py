"""Validate user-supplied repository URLs.

This is the least-trusted input in the system: the URL is handed to git, which
will happily interpret local paths, alternate transports and option-looking
arguments. An allowlist is the only safe shape here -- a blocklist of "bad"
prefixes would miss the next transport.

Rejected on purpose:
  file:// ssh:// git:// git+ssh://   non-HTTP transports and local-path reads
  anything not on github.com         SSRF into internal hosts
  leading "-"                        argument injection into the git CLI
  user:pass@ in the netloc           credential smuggling
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"github.com", "www.github.com"})

# GitHub's own rules: alphanumerics, hyphen, underscore, dot; no leading dash.
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$")


class InvalidRepoURL(ValueError):
    """Raised when a URL is not a safe, public GitHub repository URL."""


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    name: str

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_repo_url(raw: str) -> RepoRef:
    if not raw or not isinstance(raw, str):
        raise InvalidRepoURL("A repository URL is required.")

    candidate = raw.strip()
    if candidate.startswith("-"):
        raise InvalidRepoURL("URL must not start with '-'.")
    if len(candidate) > 500:
        raise InvalidRepoURL("URL is too long.")

    # Accept a bare "owner/repo" as a convenience, but nothing more exotic.
    if "://" not in candidate:
        candidate = f"https://github.com/{candidate.lstrip('/')}"

    parsed = urlparse(candidate)

    if parsed.scheme not in ("http", "https"):
        raise InvalidRepoURL(
            f"Unsupported scheme '{parsed.scheme}'. Only https GitHub URLs are allowed."
        )
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise InvalidRepoURL("Credentials must not be embedded in the URL.")
    if (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise InvalidRepoURL(f"Only github.com repositories are supported, got '{parsed.hostname}'.")

    segments = [s for s in (parsed.path or "").split("/") if s]
    if len(segments) != 2:
        raise InvalidRepoURL("URL must be of the form https://github.com/<owner>/<repo>.")

    owner, name = segments[0], segments[1]
    if name.endswith(".git"):
        name = name[: -len(".git")]

    for segment in (owner, name):
        if not _SEGMENT.match(segment) or segment in (".", ".."):
            raise InvalidRepoURL(f"Invalid path segment: '{segment}'.")

    return RepoRef(owner=owner, name=name)
