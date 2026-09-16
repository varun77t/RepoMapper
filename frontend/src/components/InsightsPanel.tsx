import { useRef, useState } from "react";

import type { Insights } from "../api/types";
import { basename, dirname } from "../lib/format";
import { clusterColor } from "../lib/palette";

/**
 * The sidebar.
 *
 * Every section is named for what the reader gets out of it, not for the
 * algorithm that produced it. No scores, no tiers, no method: someone landing
 * in an unfamiliar repository wants to know where to start, not how the
 * ranking was computed.
 */

type Section = "start" | "used" | "parts" | "loops" | "unused";

interface Props {
  insights: Insights;
  repoName: string;
  selectedId: string | null;
  nodeIdByPath: Map<string, string>;
  onSelect: (nodeId: string | null) => void;
  onHighlight: (nodeIds: string[] | null) => void;
}

export function InsightsPanel({
  insights,
  repoName,
  selectedId,
  nodeIdByPath,
  onSelect,
  onHighlight,
}: Props) {
  const [open, setOpen] = useState<Section>("start");
  const listRef = useRef<HTMLDivElement>(null);

  const counts: Record<Section, number> = {
    start: insights.reading_order.length,
    used: insights.central_files.length,
    parts: insights.communities.length,
    loops: insights.cycles.scc_count,
    unused: insights.orphans.counts.files + insights.orphans.counts.symbols_high_confidence,
  };

  const sections: { id: Section; label: string }[] = [
    { id: "start", label: "Start here" },
    { id: "used", label: "Most used" },
    { id: "parts", label: "Parts" },
    { id: "loops", label: "Loops" },
    { id: "unused", label: "Unused" },
  ];

  /**
   * Opening a section while scrolled into a long one -- Parts runs to
   * seventeen entries -- leaves the reader looking at empty space, because the
   * list collapses above them. Pull the new heading up to the top.
   */
  const toggle = (id: Section) => {
    setOpen(id);
    onHighlight(null);
    requestAnimationFrame(() => {
      const list = listRef.current;
      const heading = list?.querySelector<HTMLElement>(`[data-section="${id}"]`);
      if (list && heading) list.scrollTop = heading.offsetTop;
    });
  };

  return (
    <aside className="sidebar">
      <header className="sidebar-head">
        <h2>{repoName}</h2>
        <p>
          {insights.summary.file_count} files · {insights.summary.import_edge_count}{" "}
          connections
        </p>
      </header>

      <div className="sections" ref={listRef}>
        {sections.map((section) => {
          const isOpen = open === section.id;
          if (section.id === "loops" && counts.loops === 0) return null;
          return (
            <section key={section.id} className={isOpen ? "open" : ""}>
              <button
                className="section-head"
                data-section={section.id}
                onClick={() => toggle(section.id)}
              >
                <span className="caret" aria-hidden />
                <span className="section-label">{section.label}</span>
                <span className="section-count">{counts[section.id]}</span>
              </button>
              {isOpen && (
                <div className="section-body">
                  {section.id === "start" && (
                    <StartHere
                      insights={insights}
                      selectedId={selectedId}
                      onSelect={onSelect}
                    />
                  )}
                  {section.id === "used" && (
                    <MostUsed
                      insights={insights}
                      selectedId={selectedId}
                      onSelect={onSelect}
                    />
                  )}
                  {section.id === "parts" && (
                    <Parts
                      insights={insights}
                      onSelect={onSelect}
                      onHighlight={onHighlight}
                    />
                  )}
                  {section.id === "loops" && (
                    <Loops
                      insights={insights}
                      nodeIdByPath={nodeIdByPath}
                      onSelect={onSelect}
                    />
                  )}
                  {section.id === "unused" && (
                    <Unused
                      insights={insights}
                      nodeIdByPath={nodeIdByPath}
                      onSelect={onSelect}
                    />
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="note">{children}</p>;
}

function FileRow({
  path,
  index,
  meta,
  active,
  onClick,
}: {
  path: string;
  index?: number;
  meta?: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`file-row ${active ? "active" : ""}`} onClick={onClick}>
      {index !== undefined && <span className="index">{index}</span>}
      <span className="file-name">
        <span className="base">{basename(path)}</span>
        <span className="dir">{dirname(path) || "/"}</span>
      </span>
      {meta}
    </button>
  );
}

function StartHere({
  insights,
  selectedId,
  onSelect,
}: {
  insights: Insights;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <>
      <Note>Read them in this order. Each one builds on what came before.</Note>
      <div className="rows">
        {insights.reading_order.map((step) => (
          <FileRow
            key={step.node_id}
            path={step.path}
            index={step.step}
            active={selectedId === step.node_id}
            onClick={() => onSelect(step.node_id)}
            meta={
              step.in_cycle ? (
                <span className="tag loop">loop</span>
              ) : step.reason === "entry point" ? (
                <span className="tag">way in</span>
              ) : undefined
            }
          />
        ))}
      </div>
    </>
  );
}

function MostUsed({
  insights,
  selectedId,
  onSelect,
}: {
  insights: Insights;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const max = Math.max(1, ...insights.central_files.map((file) => file.in_degree));

  return (
    <>
      <Note>Change one of these and a lot of other files feel it.</Note>
      <div className="rows">
        {insights.central_files.map((file, position) => (
          <button
            key={file.node_id}
            className={`file-row ${selectedId === file.node_id ? "active" : ""}`}
            onClick={() => onSelect(file.node_id)}
          >
            <span className="index">{position + 1}</span>
            <span className="file-name">
              <span className="base">{basename(file.path)}</span>
              <span className="dir">{dirname(file.path) || "/"}</span>
              <span className="meter">
                <span style={{ width: `${(file.in_degree / max) * 100}%` }} />
              </span>
            </span>
            <span className="tag">{file.in_degree}</span>
          </button>
        ))}
      </div>
    </>
  );
}

function Parts({
  insights,
  onSelect,
  onHighlight,
}: {
  insights: Insights;
  onSelect: (id: string) => void;
  onHighlight: (ids: string[] | null) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);

  return (
    <>
      <Note>Groups of files that mostly work with each other.</Note>
      <div className="rows">
        {insights.communities.map((part) => {
          const isOpen = open === part.id;
          return (
            <div key={part.id}>
              <button
                className={`file-row ${isOpen ? "active" : ""}`}
                onClick={() => {
                  const next = isOpen ? null : part.id;
                  setOpen(next);
                  onHighlight(next === null ? null : part.node_ids);
                }}
              >
                <span className="swatch" style={{ background: clusterColor(part.id) }} />
                <span className="file-name">
                  <span className="base">{part.common_directory || "top level"}</span>
                  <span className="dir">
                    around {basename(part.central_paths[0] ?? "")}
                  </span>
                </span>
                <span className="tag">{part.size}</span>
              </button>
              {isOpen && (
                <div className="nested">
                  {part.node_ids.map((nodeId, index) => (
                    <button key={nodeId} onClick={() => onSelect(nodeId)}>
                      {part.paths[index]}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

function Loops({
  insights,
  nodeIdByPath,
  onSelect,
}: {
  insights: Insights;
  nodeIdByPath: Map<string, string>;
  onSelect: (id: string) => void;
}) {
  const { cycles } = insights;

  return (
    <>
      <Note>
        These depend on each other in a circle, so there is no clean place to start.
      </Note>
      <div className="rows">
        {cycles.cycles.map((cycle, index) => (
          <div className="loop-row" key={index}>
            {cycle.paths.map((path) => (
              <button
                key={path}
                onClick={() => {
                  const nodeId = nodeIdByPath.get(path);
                  if (nodeId) onSelect(nodeId);
                }}
              >
                {basename(path)}
              </button>
            ))}
          </div>
        ))}
      </div>
      {cycles.truncated && (
        <Note>Showing the first {cycles.cycles.length} of many.</Note>
      )}
    </>
  );
}

function Unused({
  insights,
  nodeIdByPath,
  onSelect,
}: {
  insights: Insights;
  nodeIdByPath: Map<string, string>;
  onSelect: (id: string) => void;
}) {
  const { orphans } = insights;
  const [showPublic, setShowPublic] = useState(false);
  const symbols = orphans.symbols.filter(
    (symbol) => showPublic || symbol.confidence === "high",
  );

  return (
    <>
      <Note>Nothing else here refers to these. Worth a look before you trust them.</Note>

      <div className="rows">
        {orphans.files.map((file) => (
          <FileRow
            key={file.node_id}
            path={file.path}
            active={false}
            onClick={() => onSelect(file.node_id)}
            meta={<span className="tag">file</span>}
          />
        ))}
        {symbols.map((symbol) => (
          <button
            key={symbol.node_id}
            className="file-row"
            onClick={() => {
              const nodeId = nodeIdByPath.get(symbol.path);
              if (nodeId) onSelect(nodeId);
            }}
          >
            <span className="file-name">
              <span className="base">{symbol.label}</span>
              <span className="dir">
                {symbol.path}:{symbol.start_line}
              </span>
            </span>
            <span className="tag">{symbol.kind}</span>
          </button>
        ))}
      </div>

      {orphans.counts.symbols_low_confidence > 0 && (
        <button className="quiet-toggle" onClick={() => setShowPublic((on) => !on)}>
          {showPublic ? "Hide" : "Show"} {orphans.counts.symbols_low_confidence} more that
          other projects could be using
        </button>
      )}
    </>
  );
}
