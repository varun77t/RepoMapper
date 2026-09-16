import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { GraphResponse, Insights, SourceFileSummary } from "../api/types";

interface RepoData {
  insights: Insights | null;
  fileGraph: GraphResponse | null;
  files: SourceFileSummary[];
  /** Graph node ids are `file:{path}`; file detail is fetched by database id. */
  fileIdByPath: Map<string, string>;
  loading: boolean;
  error: string | null;
  symbolGraph: GraphResponse | null;
  symbolGraphLoading: boolean;
  loadSymbolGraph: () => void;
}

/**
 * Loads everything the UI needs once an analysis is complete.
 *
 * The symbol graph is deliberately left until asked for: on a mid-sized repo
 * it is an order of magnitude larger than the file graph and most sessions
 * never open it.
 */
export function useRepoData(analysisId: string | null): RepoData {
  const [insights, setInsights] = useState<Insights | null>(null);
  const [fileGraph, setFileGraph] = useState<GraphResponse | null>(null);
  const [files, setFiles] = useState<SourceFileSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [symbolGraph, setSymbolGraph] = useState<GraphResponse | null>(null);
  const [symbolGraphLoading, setSymbolGraphLoading] = useState(false);

  const currentId = useRef<string | null>(null);

  useEffect(() => {
    currentId.current = analysisId;
    setSymbolGraph(null);

    if (!analysisId) {
      setInsights(null);
      setFileGraph(null);
      setFiles([]);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      api.insights(analysisId),
      api.graph(analysisId, "file"),
      api.files(analysisId),
    ])
      .then(([nextInsights, nextGraph, nextFiles]) => {
        if (cancelled) return;
        setInsights(nextInsights);
        setFileGraph(nextGraph);
        setFiles(nextFiles);
      })
      .catch((exc) => {
        if (cancelled) return;
        setError(exc instanceof ApiError ? exc.message : String(exc));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [analysisId]);

  const loadSymbolGraph = useCallback(() => {
    if (!analysisId || symbolGraph || symbolGraphLoading) return;
    setSymbolGraphLoading(true);
    api
      .graph(analysisId, "symbol")
      .then((graph) => {
        if (currentId.current === analysisId) setSymbolGraph(graph);
      })
      .catch((exc) => {
        if (currentId.current === analysisId) {
          setError(exc instanceof ApiError ? exc.message : String(exc));
        }
      })
      .finally(() => setSymbolGraphLoading(false));
  }, [analysisId, symbolGraph, symbolGraphLoading]);

  // Memoised: this map is an effect dependency in DetailPanel, and a fresh
  // Map on every render would re-trigger the fetch that caused the render.
  const fileIdByPath = useMemo(
    () => new Map(files.map((file) => [file.path, file.id])),
    [files],
  );

  return {
    insights,
    fileGraph,
    files,
    fileIdByPath,
    loading,
    error,
    symbolGraph,
    symbolGraphLoading,
    loadSymbolGraph,
  };
}
