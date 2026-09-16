# RepoMap

Point it at a GitHub repository and it tells you which files matter and what order to read them in.

RepoMap clones a repo, parses it with tree-sitter, builds a dependency and call graph in networkx, and runs graph algorithms over it: PageRank for the most depended-upon files, Louvain for subsystem clustering, SCC analysis for circular imports, and a condensation-based topological sort for a suggested onboarding path.

The analysis is real static analysis. No LLM is involved in any of it.

**Status:** backend and frontend complete (Phases 1–4). The optional LLM summary layer is not built.

---

## Quick start

```bash
docker compose up --build
```

Then open http://localhost:3000. The API docs are at http://localhost:8000/docs.

Straight to the API instead:

```bash
curl -X POST http://localhost:8000/analyze -H "Content-Type: application/json" -d "{\"repo_url\":\"https://github.com/pallets/flask\"}"
```

That returns an `analysis_id` immediately. Poll `GET /analysis/{id}` until `status` is `complete`, then read `GET /analysis/{id}/insights`.

### Local development

```bash
docker compose up -d postgres
cd backend
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
DATABASE_URL=postgresql+psycopg://repomap:repomap@localhost:5432/repomap .venv/Scripts/python -m uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm run dev
```

```bash
cd backend && .venv/Scripts/python -m pytest
```

179 tests, no network and no database required — they run the real pipeline against fixture repos on in-memory SQLite.

The frontend reads `VITE_API_BASE` (default `http://localhost:8000`) and calls the API directly rather than through a dev proxy, so the backend's `CORS_ORIGINS` must list the frontend's origin. Both defaults already cover ports 5173 and 3000.

---

## What it found in Flask

```
83 files, 198 import edges, analyzed in ~6s

Most depended-upon          src/flask/__init__.py (imported by 52)
                            src/flask/globals.py  (17)
                            src/flask/helpers.py  (10)
Circular imports            2 groups, 22 files tangled together
Read first                  src/flask/__main__.py -> the core cycle -> leaves
Dead code candidates        3 files, 15 high-confidence symbols
```

Express, for contrast: 141 files, 153 import edges, no cycles at all, and `index.js`
imported by 96 others.

---

## The interface

A field of dots on a warm near-black canvas: each dot is a file, its size is
how much the rest of the project leans on it, and dashed lines run from a file
to what it imports. Files are boxed by the folder they live in and coloured by
the top-level area that folder belongs to, so `backend/` reads as one colour
family split into its own sub-areas and `frontend/` as another. Only the files
carrying real weight take the colour at full strength; the rest are muted, so
the canvas reads as quiet with emphasis rather than as confetti. Click one and
the rest of the project fades back, leaving that file, what it uses and what
uses it.

The whole field turns, slowly — about three minutes to the revolution — and
stops the moment anything is selected, searched or highlighted, because text
that will not hold still is worse than no motion at all.

Down the left, five sections: **Start here**, **Most used**, **Parts**,
**Loops**, **Unused**. Each is named for what the reader gets out of it. No
scores, no confidence tiers, no algorithm names — someone landing in an
unfamiliar repository wants to know where to begin, not how the ranking was
computed. The same restraint governs the wait: the API reports `cloning`,
`parsing`, `building_graph`, and the screen says "Fetching the code", "Reading
every file", "Following the connections".

The methodology in this README is deliberately absent from the product.

## API

| Endpoint | Purpose |
|---|---|
| `POST /analyze` | Queue a repo. Returns `analysis_id` immediately; `cached: true` if this commit was already analyzed. |
| `GET /analysis/{id}` | Status, progress, and call-resolution statistics. |
| `GET /analysis/{id}/entities` | Raw extraction — every file, symbol and import, before any graph work. |
| `GET /analysis/{id}/graph?level=file\|symbol\|all` | Flat `{nodes, edges}`. |
| `GET /analysis/{id}/insights` | All five algorithms. |
| `GET /analysis/{id}/files` and `/files/{file_id}` | A file's symbols, what it imports, what imports it. |
| `GET /analysis/{id}/calls?resolution=local` | Call edges filtered by resolution tier. |

---

## Architecture

```
ingestion/   clone + walk         GitPython, URL allowlist
parsing/     AST -> dataclasses   tree-sitter, per-language extractors
  resolution/                     import + call resolution
graphing/    dataclasses -> graph networkx DiGraph
algorithms/  graph -> insights    PageRank, Louvain, SCC, topological sort
services/    orchestration        pipeline, background jobs
api/         HTTP                 FastAPI

frontend/src/
  api/       typed client         mirrors the response contract
  lib/       graph mapping        API shape -> Cytoscape elements, styles, layouts
  hooks/     data flow            analyse-and-poll, lazy graph loading
  components/UI                   graph canvas, insight tabs, file detail
```

The layering is enforced by tests, not just documented: `tests/test_layering.py` imports each layer in a subprocess and asserts that `algorithms/`, `parsing/` and `graphing/` never pull in SQLAlchemy or FastAPI. That is what lets the algorithms be unit-tested against synthetic graphs with no database or HTTP server.

`analyze_directory(path)` takes a directory and returns plain dataclasses — the entire analysis core is usable without a web server.

### Edge direction is a contract

`A -> B` means **A imports B**. Depended-upon files therefore accumulate *incoming* edges and rank highly under PageRank with no graph reversal anywhere. Flipping this would silently invert the central-files ranking.

---

## Notes on the hard parts

**Cycles are condensed before PageRank.** A circular-import group is a spider trap: rank flows in and then circulates between its members forever, while a genuinely central leaf module (imported by many, importing nothing) is a dangling node whose rank is redistributed away. On the test fixture, raw PageRank ranks a 2-file cycle 1st and 2nd and `config.py` — imported four times — only 3rd. Collapsing each strongly connected component removes the trap by construction. Each member inherits the component's score **in full**; dividing by component size was worse, because it penalises cycle membership and ranked `flask/typing.py` (in-degree 1) above `flask/__init__.py` (in-degree 52).

**Cycle enumeration is bounded.** `nx.simple_cycles(G)` is exponential in the number of cycles and will hang on a real repo. Strongly connected components are computed first (linear, and an SCC larger than one node already proves a cycle exists), then concrete cycles are enumerated only inside each SCC with a length bound and a count cap. Flask hits the cap — the response says `truncated: true` rather than pretending the list is complete.

**Reading order sorts topologically, not by BFS depth.** Depth-first ordering inverts edges: with `main -> y`, `main -> p -> q -> x` and `x -> y`, `y` sits at depth 1 and `x` at depth 3, so depth ordering would place `y` before the file that imports it.

**Call resolution is tiered and reports its own accuracy.** Resolving every call correctly needs real type inference. Instead each call is resolved in confidence order and the tier is recorded: `local` (1.0), `import` (0.9), `heuristic` — unique name repo-wide (0.5), `builtin`, `unresolved`. Builtins are counted separately because scoring `print()` as a resolution failure would understate accuracy badly. Flask reports 35.2% resolved and 12.0% high-confidence, Express 44.0% and 4.3%; the fixtures report 100%. Import resolution, which is what all five algorithms actually depend on, resolves completely on every repo tested.

**The centrality list ranks by PageRank and draws its bars from in-degree.**
Because every member of a strongly connected component inherits the
component's score in full, Flask's 22-file circular core produces 22 files with
an identical score of 0.281 — bars drawn from that are 22 full-width blocks
saying nothing. In-degree is what actually separates those files (52 importers
for `__init__.py`, 17 for `globals.py`), so that is what the bar shows, and the
score itself never reaches the screen. This only surfaced on a real repository;
the test fixture's cycle is two files.

**Cytoscape is created once and never rebuilt.** The instance lives in a ref
behind an effect with no dependencies, with element replacement, the entrance
cascade, emphasis and viewport fitting each in their own effect. Selection,
search and cluster highlighting are computed in a single pass, because two
effects both adding and removing a `dim` class fight over it. A `ResizeObserver`
calls `cy.resize()`, since Cytoscape caches its container size and would
otherwise keep rendering at the old width when the detail panel opens.

**Nodes arrive largest first.** One `requestAnimationFrame` drives the whole
entrance over a fixed 750ms however many nodes there are — a timer per node
would be several thousand of them on a symbol graph. Edge dashes drift on a
second rAF, throttled to 30fps and skipped entirely above 420 edges.

**A source root is the parent of a top-level package.** Counting only the repo
root, `src/`, and directories holding packaging metadata misses the single most
common shape of a deployed application: `backend/app/...` with nothing but a
`requirements.txt` beside it. Every `import app.config` then fell through to
"external" and the repo came back with 40 files and *one* edge — a graph whose
five algorithms all had nothing to work with. A package's importable name is
rooted at the first ancestor that is not itself a package, so `backend/` goes
on the path and the module is `app.config`.

**tsconfig `references` have to be followed.** Vite's scaffold writes a
tsconfig.json containing nothing but references to tsconfig.app.json and
tsconfig.node.json, and puts `paths` in the referenced file. Reading only the
canonical filename finds an empty `compilerOptions`, so every `@/...` import
resolves to "external" on a stock React project. Aliases are now merged from
every tsconfig*/jsconfig* in the tree, each resolved against its own directory
so a monorepo with two frontends keeps them apart.

**The cache is keyed on the analyzer, not just the commit.** Results are cached
per (repo_url, commit_sha), which is only sound while the analysis never
changes. Both fixes above alter the output for unchanged input, and without a
version in the key a user who re-ran the same repository would have been served
the broken graph forever with nothing to indicate why. `ANALYZER_VERSION` is
recorded in its own table — `create_all` adds missing tables but never alters
existing ones, so a new column would simply not appear — and cached rows from
an older version are dropped at startup.

**Folder grouping depth adapts to the repo.** Boxing every file by its own
directory reads well on a 40-file project and becomes twenty overlapping
rectangles on Flask. The depth is chosen per repository instead: the most
detailed grouping that still fits under a cap.

**The function view draws 140 nodes, not 760.** Flask has 760 connected
functions; drawn together they are a field of identical dots with labels too
small to render — complete and unreadable. The view shows the busiest, plus
everything belonging to the file the reader has open. That focus is held
separately from the selection, so clicking a function does not re-scope the
view and re-run the layout under the cursor.

**Entry-point conventions are split by strength.** `__main__.py` and `index.ts` mean "run me" at any depth. `app.py` and `cli.py` only count near the repo root — `src/flask/app.py` is an ordinary library module, and treating it as an entry point pollutes the reading order of every library.

---

## Known limitations

These are design boundaries, not bugs to be surprised by later.

- **Call resolution is best-effort.** Dynamic dispatch, monkey-patching and reflection are invisible. Use the `resolution` tier on each edge to filter.
- **Orphan detection reports candidates, not findings.** A symbol passed *by reference* — a click callback, an event handler, a registry entry — looks identical to dead code, because only call edges are tracked. Exported symbols are marked low confidence for the same reason.
- **Vite and webpack aliases are not resolved.** `tsconfig.json` / `jsconfig.json` `baseUrl` and `paths` are read; aliases defined only in JS config files would require evaluating arbitrary JavaScript.
- **Jobs run in-process.** A restart loses running jobs; they are marked failed on the next boot so clients stop polling. The deployment is pinned to one worker because a second one would not see the first's queue. Celery/RQ is the upgrade path, not warranted yet.
- **The frontend holds the whole graph in memory.** Both graph levels are fetched
  whole and mapped to Cytoscape elements client-side. That is fine for the 5000-file
  ingestion cap, and the symbol view already filters isolated nodes, but there is no
  server-side pagination or viewport culling — a repo an order of magnitude larger
  would need both.
- **No Alembic yet.** The schema is created with `create_all` at startup. The schema churned heavily through the parsing and graph phases and writing migrations against a moving target would have been wasted work. This is the first thing to add before the schema is shared with anyone.

## Dependency pinning

The four tree-sitter packages are **pinned as a set** and must not be bumped individually. `tree-sitter-typescript` is stalled at 0.23.2 and requires `tree-sitter~=0.23`, while `tree-sitter-python` and `tree-sitter-javascript` at 0.25 require `tree-sitter~=0.24`; tree-sitter is backwards- but not forwards-compatible on grammar ABI, and 0.25 broke ABI v13/14. The 0.23 line is the newest where all four agree.

`numpy` and `scipy` are explicit dependencies because `nx.pagerank` dispatches to a SciPy implementation and networkx does not declare them itself.

## Roadmap

Phase 5 — optional per-file plain-English summaries via Gemini, on demand, behind `ENABLE_LLM_SUMMARIES`. The `Summarizer` protocol and a `NullSummarizer` already exist in `app/llm/summarizer.py`; nothing in `parsing/`, `graphing/` or `algorithms/` imports it, and the app is fully functional without it.
