import { type FormEvent, useState } from "react";

import type { AnalysisState } from "../api/types";
import type { Phase } from "../hooks/useAnalysis";
import { Starfield } from "./Starfield";

/**
 * Progress in the user's language.
 *
 * The API reports its own pipeline stages -- cloning, parsing, building_graph
 * -- which describe how the work is done, not what is happening to their
 * repository. Nobody waiting on a page needs to know a graph is being built.
 */
const STAGES: { key: string; text: string }[] = [
  { key: "queued", text: "Getting ready" },
  { key: "cloning", text: "Fetching the code" },
  { key: "parsing", text: "Reading every file" },
  { key: "building_graph", text: "Following the connections" },
  { key: "computing_insights", text: "Working out what matters" },
];

const EXAMPLES = [
  { label: "flask", url: "https://github.com/pallets/flask" },
  { label: "express", url: "https://github.com/expressjs/express" },
];

interface Props {
  phase: Phase;
  state: AnalysisState | null;
  error: string | null;
  onSubmit: (repoUrl: string) => void;
}

export function Landing({ phase, state, error, onSubmit }: Props) {
  const [value, setValue] = useState("");
  const busy = phase === "starting" || phase === "running";

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (trimmed && !busy) onSubmit(trimmed);
  }

  const stageIndex = state ? STAGES.findIndex((s) => s.key === state.status) : 0;
  const stage = STAGES[Math.max(0, stageIndex)] ?? STAGES[0];

  return (
    <div className="landing">
      <Starfield />

      <div className="landing-inner">
        <h1 className="wordmark">RepoMap</h1>
        <p className="tagline">Find your way around a codebase you've never seen.</p>

        {busy ? (
          <div className="working">
            <div className="working-stages">
              {STAGES.map((entry, index) => (
                <span
                  key={entry.key}
                  className={
                    index < stageIndex ? "done" : index === stageIndex ? "now" : ""
                  }
                />
              ))}
            </div>
            <p className="working-text" key={stage.key}>
              {stage.text}
            </p>
          </div>
        ) : (
          <>
            <form className="hero-form" onSubmit={submit}>
              <input
                aria-label="GitHub repository link"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                placeholder="Paste a GitHub link"
                spellCheck={false}
                autoFocus
              />
              <button type="submit" disabled={!value.trim()} aria-label="Open">
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
                  <path
                    d="M5 12h13M12 5l7 7-7 7"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            </form>

            <p className="try">
              or try
              {EXAMPLES.map((example) => (
                <button key={example.url} onClick={() => onSubmit(example.url)}>
                  {example.label}
                </button>
              ))}
            </p>
          </>
        )}

        {phase === "error" && error && <p className="hero-error">{error}</p>}
      </div>
    </div>
  );
}
