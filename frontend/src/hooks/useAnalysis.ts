import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AnalysisState } from "../api/types";

export type Phase = "idle" | "starting" | "running" | "ready" | "error";

const POLL_INTERVAL_MS = 1200;

/**
 * Starts an analysis and polls it to a terminal state.
 *
 * POST /analyze returns 202 immediately -- cloning and parsing happen on a
 * background thread -- so the only way to know when results exist is to poll
 * GET /analysis/{id} until it reports complete or failed.
 */
export function useAnalysis() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [state, setState] = useState<AnalysisState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cached, setCached] = useState(false);

  // Incremented on every start so a poll from an abandoned run cannot write
  // its result over the current one.
  const runToken = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPolling = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const start = useCallback(
    async (repoUrl: string) => {
      stopPolling();
      const token = ++runToken.current;

      setPhase("starting");
      setError(null);
      setState(null);
      setCached(false);

      try {
        const started = await api.analyze(repoUrl.trim());
        if (token !== runToken.current) return;
        setCached(started.cached);
        setPhase("running");

        const poll = async () => {
          if (token !== runToken.current) return;
          try {
            const current = await api.status(started.analysis_id);
            if (token !== runToken.current) return;
            setState(current);

            if (current.status === "complete") {
              setPhase("ready");
              return;
            }
            if (current.status === "failed") {
              setError(current.error ?? "Analysis failed.");
              setPhase("error");
              return;
            }
            timer.current = setTimeout(poll, POLL_INTERVAL_MS);
          } catch (exc) {
            if (token !== runToken.current) return;
            setError(exc instanceof ApiError ? exc.message : String(exc));
            setPhase("error");
          }
        };

        await poll();
      } catch (exc) {
        if (token !== runToken.current) return;
        setError(exc instanceof ApiError ? exc.message : String(exc));
        setPhase("error");
      }
    },
    [stopPolling],
  );

  const reset = useCallback(() => {
    stopPolling();
    runToken.current++;
    setPhase("idle");
    setState(null);
    setError(null);
    setCached(false);
  }, [stopPolling]);

  return { phase, state, error, cached, start, reset };
}
