"""Version of the analysis itself.

Results are cached per (repo_url, commit_sha), which is correct as long as the
analyzer never changes. It does: import resolution in particular has been
wrong in ways that produced a graph with almost no edges, and a user who
re-ran the same repository after the fix would have been served the broken
result forever, with nothing to indicate why.

Bump this whenever a change alters the output for unchanged input -- parsing,
import or call resolution, graph construction, or any algorithm. Cached rows
from an older version are dropped at startup and recomputed on demand.

History:
  1  first release
  2  package parents count as Python source roots; tsconfig `references` are
     followed for path aliases
"""

ANALYZER_VERSION = "2"
