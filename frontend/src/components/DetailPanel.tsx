import { useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { FileDetail, Insights } from "../api/types";
import { basename, dirname } from "../lib/format";
import { clusterColor } from "../lib/palette";

interface Props {
  analysisId: string;
  path: string | null;
  insights: Insights;
  fileIdByPath: Map<string, string>;
  onSelectPath: (path: string) => void;
  onClose: () => void;
}

/**
 * Detail for the selected file.
 *
 * Graph nodes are keyed by path while the detail endpoint takes a database id,
 * so the path is mapped through the file list the app already holds rather
 * than by rebuilding ids on the client.
 */
export function DetailPanel({
  analysisId,
  path,
  insights,
  fileIdByPath,
  onSelectPath,
  onClose,
}: Props) {
  const [detail, setDetail] = useState<FileDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fileId = path ? fileIdByPath.get(path) : undefined;
    if (!fileId) {
      setDetail(null);
      return;
    }

    let cancelled = false;
    setError(null);
    api
      .fileDetail(analysisId, fileId)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch((exc) => {
        if (!cancelled) setError(exc instanceof ApiError ? exc.message : String(exc));
      });

    return () => {
      cancelled = true;
    };
  }, [analysisId, path, fileIdByPath]);

  if (!path) return null;

  // A file can import the same module on several lines -- three `from x import`
  // statements are three rows from the API and would read as a bug here.
  const uses = detail
    ? [
        ...new Set(
          detail.imports
            .map((imported) => imported.resolved_path)
            .filter((target): target is string => Boolean(target)),
        ),
      ].sort()
    : [];

  const step = insights.reading_order.find((entry) => entry.path === path);
  const part = insights.communities.find((entry) => entry.paths.includes(path));
  const isEntry = insights.entry_points.some((entry) => entry.path === path);

  return (
    <aside className="detail">
      <button className="close" onClick={onClose} aria-label="Close">
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
          <path
            d="M6 6l12 12M18 6L6 18"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      </button>

      <h2>{basename(path)}</h2>
      <p className="detail-path">{dirname(path) || "/"}</p>

      <div className="tags">
        {isEntry && <span className="tag">way in</span>}
        {step && (
          <span className={`tag ${step.in_cycle ? "loop" : ""}`}>read #{step.step}</span>
        )}
        {step?.in_cycle && <span className="tag loop">in a loop</span>}
        {part && (
          <span className="tag">
            <span className="swatch" style={{ background: clusterColor(part.id) }} />
            {part.common_directory || "top level"}
          </span>
        )}
      </div>

      {error && <p className="note">{error}</p>}

      {detail && (
        <>
          <p className="note">
            {detail.file.language} · {detail.file.line_count} lines
          </p>
          {detail.file.parse_error && (
            <p className="note warn">Some of this file could not be read.</p>
          )}

          <Section title={`Used by ${detail.imported_by.length}`} empty="Nothing uses it.">
            {detail.imported_by.map((importer) => (
              <button key={importer} onClick={() => onSelectPath(importer)}>
                {importer}
              </button>
            ))}
          </Section>

          <Section title={`Uses ${uses.length}`} empty="Uses nothing from this project.">
            {uses.map((target) => (
              <button key={target} onClick={() => onSelectPath(target)}>
                {target}
              </button>
            ))}
          </Section>

          <Section title={`Inside ${detail.symbols.length}`} empty="Nothing defined here.">
            {detail.symbols.map((symbol) => (
              <span key={symbol.id} className="symbol">
                <i className={symbol.kind} />
                {symbol.qualified_name}
                <em>{symbol.start_line}</em>
              </span>
            ))}
          </Section>
        </>
      )}
    </aside>
  );
}

function Section({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string;
  children: React.ReactNode[];
}) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children.length === 0 ? (
        <p className="note">{empty}</p>
      ) : (
        <div className="links">{children}</div>
      )}
    </section>
  );
}
