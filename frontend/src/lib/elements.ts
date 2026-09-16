import type { ElementDefinition } from "cytoscape";

import type { FileNode, GraphResponse, Insights, SymbolNode } from "../api/types";
import { INK, clusterColor, mute } from "./palette";

/**
 * The single boundary between the API's flat {nodes, edges} shape and
 * Cytoscape's {data: {...}} element format. Everything the stylesheet reads --
 * colour, size, pill fill, whether a node is named at rest -- is computed here.
 */

export interface Decorations {
  entryPoints: Set<string>;
  inCycle: Set<string>;
  readingStep: Map<string, number>;
}

export interface ElementBundle {
  elements: ElementDefinition[];
  nodeCount: number;
  edgeCount: number;
}

export function decorationsFrom(insights: Insights | null): Decorations {
  const entryPoints = new Set<string>();
  const inCycle = new Set<string>();
  const readingStep = new Map<string, number>();
  if (!insights) return { entryPoints, inCycle, readingStep };

  for (const entry of insights.entry_points) entryPoints.add(entry.node_id);
  for (const step of insights.reading_order) {
    readingStep.set(step.node_id, step.step);
    if (step.in_cycle) inCycle.add(step.node_id);
  }
  return { entryPoints, inCycle, readingStep };
}

/**
 * Files are boxed by the folder they live in and coloured by the top-level
 * area that folder belongs to, so `backend/...` reads as one colour family
 * split into its own sub-areas and `frontend/...` as another. Directories are
 * used rather than the detected clusters because they are what the reader
 * already has a mental model of -- the dependency clusters remain available
 * in the sidebar.
 */
function areaOf(path: string): string {
  const cut = path.indexOf("/");
  return cut === -1 ? "" : path.slice(0, cut);
}

function folderOf(path: string, depth: number): string {
  const cut = path.lastIndexOf("/");
  if (cut === -1) return "";
  return path.slice(0, cut).split("/").slice(0, depth).join("/");
}

/**
 * How many boxes the canvas can hold before they start colliding.
 *
 * Grouping every file by its own directory reads beautifully on a 40-file
 * project and turns into twenty overlapping rectangles on Flask. The depth is
 * chosen per repo instead: the most detailed grouping that still fits.
 */
const MAX_GROUPS = 14;

function chooseFolderDepth(paths: string[]): number {
  let best = 1;
  for (let depth = 1; depth <= 4; depth++) {
    const groups = new Set(paths.map((path) => folderOf(path, depth)));
    groups.delete("");
    if (groups.size === 0) continue;
    if (groups.size > MAX_GROUPS) break;
    best = depth;
  }
  return best;
}

/** `backend/app/models` reads as "app/models"; shorter paths stay whole. */
function folderLabel(folder: string): string {
  const parts = folder.split("/");
  return parts.length <= 2 ? folder : parts.slice(-2).join("/");
}

const MIN_SIZE = 11;
const MAX_SIZE = 32;

/** How many nodes carry a visible label at rest. */
const NAMED_COUNT = 8;

/**
 * Below this share of the top score a file gets a muted version of its area's
 * colour rather than the full-strength one. Painting every file at full
 * strength turns the canvas into confetti; the two tiers keep the emphasis on
 * what the rest of the project actually leans on.
 */
const COLOUR_THRESHOLD = 0.12;

/**
 * Importance is heavily skewed -- on Flask the top file scores forty times the
 * median -- so the scale is square-rooted. Without it every node but one
 * collapses to the minimum dot.
 */
function sizeFor(normalized: number): number {
  return MIN_SIZE + (MAX_SIZE - MIN_SIZE) * Math.sqrt(Math.max(0, normalized));
}

export function buildFileElements(
  graph: GraphResponse,
  decorations: Decorations,
): ElementBundle {
  const nodes = graph.nodes as FileNode[];
  const maxRank = nodes.reduce((max, n) => Math.max(max, n.pagerank ?? 0), 0);

  // A file's score and its in-degree disagree inside a circular group, where
  // every member shares one score. Ranking the labels by both keeps the names
  // on the files people actually recognise as the centre.
  const named = new Set(
    [...nodes]
      .sort(
        (a, b) =>
          (b.pagerank ?? 0) - (a.pagerank ?? 0) || b.in_degree - a.in_degree,
      )
      .slice(0, NAMED_COUNT)
      .map((n) => n.id),
  );
  for (const id of decorations.entryPoints) named.add(id);

  // Areas get a stable colour by sorted position, so the same repo always
  // paints the same way.
  const areas = [...new Set(nodes.map((node) => areaOf(node.path)))].sort();
  const areaColor = new Map(areas.map((area, index) => [area, clusterColor(index)]));

  const depth = chooseFolderDepth(nodes.map((node) => node.path));
  const folders = new Map<string, string>();
  for (const node of nodes) {
    const folder = folderOf(node.path, depth);
    if (folder) folders.set(folder, areaOf(node.path));
  }

  const elements: ElementDefinition[] = [...folders].map(([folder, area]) => ({
    group: "nodes" as const,
    data: {
      id: `folder:${folder}`,
      label: folderLabel(folder),
      kind: "folder",
      path: folder,
      color: areaColor.get(area) ?? INK.dim,
    },
  }));

  const colorOf = new Map<string, string>();
  elements.push(...nodes.map((node) => {
    const weight = maxRank > 0 ? (node.pagerank ?? 0) / maxRank : 0;
    const area = areaColor.get(areaOf(node.path)) ?? INK.dim;
    const color = weight >= COLOUR_THRESHOLD ? area : mute(area);
    const folder = folderOf(node.path, depth);
    colorOf.set(node.id, color);
    return {
      group: "nodes" as const,
      data: {
        id: node.id,
        parent: folder ? `folder:${folder}` : undefined,
        label: node.label,
        path: node.path,
        kind: "file",
        language: node.language,
        lineCount: node.line_count,
        symbolCount: node.symbol_count,
        importance: node.pagerank ?? 0,
        usedBy: node.in_degree,
        uses: node.out_degree,
        cluster: node.community,
        color,
        // The pill always carries the area colour, even when the dot is grey:
        // a named node is one worth telling apart. It is passed solid --
        // Cytoscape drops the alpha channel on text-background-color, so the
        // tint comes from text-background-opacity in the stylesheet.
        pill: area,
        size: sizeFor(weight),
        named: named.has(node.id),
        entryPoint: decorations.entryPoints.has(node.id),
        inCycle: decorations.inCycle.has(node.id),
        readingStep: decorations.readingStep.get(node.id) ?? null,
      },
    };
  }));

  for (const edge of graph.edges) {
    elements.push({
      group: "edges" as const,
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        kind: edge.type,
        color: colorOf.get(edge.source) ?? INK.soft,
      },
    });
  }

  return { elements, nodeCount: nodes.length, edgeCount: graph.edges.length };
}

const SYMBOL_COLORS: Record<string, string> = {
  function: "#c9b98f",
  method: "#5fa39b",
  class: "#7c5cd6",
};

/**
 * How many functions the view will draw at once.
 *
 * Flask has 760 connected functions. Drawing them all produces a field of
 * identical dots with no legible structure and labels too small to render --
 * technically complete and useless to read. The view shows the busiest ones,
 * plus everything belonging to the file the reader has open.
 */
const MAX_SYMBOL_NODES = 140;

export function buildSymbolElements(
  graph: GraphResponse,
  focusPath?: string | null,
): ElementBundle {
  const nodes = graph.nodes as SymbolNode[];

  // Most symbols in a real codebase are never called from anywhere the graph
  // can see -- Flask yields 1622 of them against 826 connections. Drawing the
  // unconnected ones buries the shape of the code in a cloud of loose dots.
  const degree = new Map<string, number>();
  const neighbours = new Map<string, Set<string>>();
  const link = (a: string, b: string) => {
    if (!neighbours.has(a)) neighbours.set(a, new Set());
    neighbours.get(a)!.add(b);
  };
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    link(edge.source, edge.target);
    link(edge.target, edge.source);
  }

  const connected = nodes.filter((n) => (degree.get(n.id) ?? 0) > 0);
  const byBusiest = [...connected].sort(
    (a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0),
  );

  // The open file's own functions come first and are never trimmed away --
  // they are the reason the reader switched to this view.
  const keep = new Set<string>();
  if (focusPath) {
    for (const node of connected) {
      if (node.path !== focusPath) continue;
      keep.add(node.id);
      for (const id of neighbours.get(node.id) ?? []) keep.add(id);
    }
  }
  for (const node of byBusiest) {
    if (keep.size >= MAX_SYMBOL_NODES) break;
    keep.add(node.id);
  }

  const visible = connected.filter((n) => keep.has(n.id));
  const maxDegree = visible.reduce((max, n) => Math.max(max, degree.get(n.id) ?? 0), 1);

  const named = new Set(
    [...visible]
      .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))
      .slice(0, NAMED_COUNT)
      .map((n) => n.id),
  );

  const colorOf = new Map<string, string>();
  const elements: ElementDefinition[] = visible.map((node) => {
    const color = SYMBOL_COLORS[node.type] ?? INK.dim;
    colorOf.set(node.id, color);
    return {
      group: "nodes" as const,
      data: {
        id: node.id,
        label: node.label,
        path: node.path,
        kind: node.type,
        name: node.name,
        startLine: node.start_line,
        usedBy: degree.get(node.id) ?? 0,
        color,
        pill: color,
        size: sizeFor((degree.get(node.id) ?? 0) / maxDegree),
        named: named.has(node.id),
      },
    };
  });

  let edgeCount = 0;
  for (const edge of graph.edges) {
    if (!keep.has(edge.source) || !keep.has(edge.target)) continue;
    edgeCount++;
    elements.push({
      group: "edges" as const,
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        kind: edge.type,
        color: colorOf.get(edge.source) ?? INK.soft,
      },
    });
  }

  return { elements, nodeCount: visible.length, edgeCount };
}
