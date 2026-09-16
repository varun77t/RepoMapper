import type { GraphLevel } from "../api/types";

interface Props {
  level: GraphLevel;
  onLevel: (level: GraphLevel) => void;
  search: string;
  onSearch: (value: string) => void;
  onFit: () => void;
  onRestart: () => void;
}

/**
 * Floats over the canvas so the graph runs edge to edge.
 *
 * Deliberately four controls. Earlier versions offered a layout algorithm
 * picker and a colour-by menu; both asked the reader to make a choice they had
 * no basis for making, so the app makes them instead.
 */
export function Toolbar({ level, onLevel, search, onSearch, onFit, onRestart }: Props) {
  return (
    <div className="toolbar">
      <button className="mark" onClick={onRestart} title="Start over">
        RepoMap
      </button>

      <div className="switch">
        <button
          className={level === "file" ? "on" : ""}
          onClick={() => onLevel("file")}
        >
          Files
        </button>
        <button
          className={level === "symbol" ? "on" : ""}
          onClick={() => onLevel("symbol")}
        >
          Functions
        </button>
      </div>

      <input
        className="find"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        placeholder="Find a file"
        spellCheck={false}
        aria-label="Find a file"
      />

      <button className="fit" onClick={onFit} title="Fit to screen">
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
          <path
            d="M4 9V5a1 1 0 011-1h4M20 9V5a1 1 0 00-1-1h-4M4 15v4a1 1 0 001 1h4M20 15v4a1 1 0 01-1 1h-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      </button>
    </div>
  );
}
