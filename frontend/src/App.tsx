import { useCallback, useMemo, useState } from "react";

import type { GraphLevel } from "./api/types";
import { basename } from "./lib/format";
import { DetailPanel } from "./components/DetailPanel";
import { GraphView } from "./components/GraphView";
import { InsightsPanel } from "./components/InsightsPanel";
import { Landing } from "./components/Landing";
import { Toolbar } from "./components/Toolbar";
import { useAnalysis } from "./hooks/useAnalysis";
import { useRepoData } from "./hooks/useRepoData";
import {
  type ElementBundle,
  buildFileElements,
  buildSymbolElements,
  decorationsFrom,
} from "./lib/elements";

const EMPTY_BUNDLE: ElementBundle = { elements: [], nodeCount: 0, edgeCount: 0 };

export default function App() {
  const analysis = useAnalysis();
  const analysisId = analysis.phase === "ready" ? (analysis.state?.analysis_id ?? null) : null;
  const data = useRepoData(analysisId);

  const [level, setLevel] = useState<GraphLevel>("file");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string[] | null>(null);
  const [fitToken, setFitToken] = useState(0);
  // The file the function view is built around. Held separately from the
  // selection so that clicking a function does not re-scope the view and
  // re-run the layout underneath the reader's cursor.
  const [focusPath, setFocusPath] = useState<string | null>(null);

  const decorations = useMemo(() => decorationsFrom(data.insights), [data.insights]);

  const nodeIdByPath = useMemo(() => {
    const map = new Map<string, string>();
    for (const node of data.fileGraph?.nodes ?? []) map.set(node.path, node.id);
    return map;
  }, [data.fileGraph]);

  const selectedPath = useMemo(() => {
    if (!selectedId) return null;
    const source = level === "file" ? data.fileGraph : data.symbolGraph;
    return source?.nodes.find((node) => node.id === selectedId)?.path ?? null;
  }, [selectedId, level, data.fileGraph, data.symbolGraph]);

  const bundle = useMemo(() => {
    if (level === "file") {
      return data.fileGraph ? buildFileElements(data.fileGraph, decorations) : EMPTY_BUNDLE;
    }
    return data.symbolGraph
      ? buildSymbolElements(data.symbolGraph, focusPath)
      : EMPTY_BUNDLE;
  }, [level, data.fileGraph, data.symbolGraph, decorations, focusPath]);

  /**
   * A pick from the sidebar can name a node the current view does not hold --
   * every sidebar list is about files, but the function view may be open.
   * Switch views rather than silently doing nothing.
   */
  const selectNode = useCallback(
    (nodeId: string | null) => {
      setHighlight(null);
      const file = data.fileGraph?.nodes.find((node) => node.id === nodeId);
      if (nodeId && file) {
        if (level === "symbol") setLevel("file");
        setFocusPath(file.path);
      }
      setSelectedId(nodeId);
    },
    [level, data.fileGraph],
  );

  const selectPath = useCallback(
    (path: string) => {
      const nodeId = nodeIdByPath.get(path);
      if (nodeId) selectNode(nodeId);
    },
    [nodeIdByPath, selectNode],
  );

  const changeLevel = useCallback(
    (next: GraphLevel) => {
      setLevel(next);
      setSelectedId(null);
      setHighlight(null);
      if (next === "symbol") data.loadSymbolGraph();
    },
    [data],
  );

  const restartFocus = useCallback(() => {
    setFocusPath(null);
    setSelectedId(null);
  }, []);

  const restart = useCallback(() => {
    setSelectedId(null);
    setHighlight(null);
    setSearch("");
    setLevel("file");
    setFocusPath(null);
    analysis.reset();
  }, [analysis]);

  if (analysis.phase !== "ready" || !data.insights || !analysisId) {
    return (
      <Landing
        phase={analysis.phase}
        state={analysis.state}
        error={analysis.error ?? data.error}
        onSubmit={analysis.start}
      />
    );
  }

  return (
    <div className="workspace">
      <InsightsPanel
        insights={data.insights}
        repoName={analysis.state?.repo_name ?? ""}
        selectedId={selectedId}
        nodeIdByPath={nodeIdByPath}
        onSelect={selectNode}
        onHighlight={setHighlight}
      />

      <main className="stage">
        <GraphView
          bundle={bundle}
          selectedId={selectedId}
          search={search}
          highlight={highlight}
          fitToken={fitToken}
          onSelect={selectNode}
        />

        <Toolbar
          level={level}
          onLevel={changeLevel}
          search={search}
          onSearch={setSearch}
          onFit={() => setFitToken((token) => token + 1)}
          onRestart={restart}
        />

        {level === "symbol" && !data.symbolGraph && (
          <p className="stage-note">
            {data.symbolGraphLoading ? "Loading…" : "Nothing to show here."}
          </p>
        )}

        {level === "symbol" && data.symbolGraph && (
          <p className="caption">
            {focusPath ? (
              <>
                What the code in <b>{basename(focusPath)}</b> calls
                <button onClick={restartFocus}>show the busiest instead</button>
              </>
            ) : (
              <>The busiest functions. Pick a file to narrow this down.</>
            )}
          </p>
        )}

        {selectedPath && (
          <DetailPanel
            analysisId={analysisId}
            path={selectedPath}
            insights={data.insights}
            fileIdByPath={data.fileIdByPath}
            onSelectPath={selectPath}
            onClose={() => setSelectedId(null)}
          />
        )}
      </main>
    </div>
  );
}
