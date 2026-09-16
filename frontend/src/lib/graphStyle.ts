// cytoscape 3.34 ships its own type definitions; @types/cytoscape is stale
// and names this type differently, so it is deliberately not installed.
import type { LayoutOptions, StylesheetJson } from "cytoscape";

import { INK } from "./palette";

/**
 * Node labels are drawn as pills -- a small dot with the name floating beside
 * it on a tinted rounded background. Cytoscape's text-background-* properties
 * do this natively, so the pill is part of the label rather than a second node
 * to keep in sync.
 *
 * Only nodes carrying a `named` flag show a label at rest. Labelling all of
 * them turns the canvas into a wall of text; the rest reveal themselves on
 * hover and on selection.
 */
export function buildStylesheet(): StylesheetJson {
  return [
    {
      selector: "node",
      style: {
        "background-color": "data(color)",
        "background-opacity": 0.95,
        label: "",
        "font-family": "DM Sans, sans-serif",
        "font-size": 11,
        "font-weight": 500,
        color: INK.text,
        "text-valign": "center",
        "text-halign": "right",
        "text-margin-x": 7,
        "text-background-shape": "round-rectangle",
        "text-background-padding": 5,
        "text-border-opacity": 0,
        "min-zoomed-font-size": 5,
        "border-width": 0,
        "overlay-opacity": 0,
        "transition-property": "opacity, width, height, border-width",
        "transition-duration": 180,
      },
    },
    // Both mappings are scoped to elements that actually carry the field.
    // An unscoped data() mapping makes Cytoscape warn once per element per
    // render, which with a rotating graph means tens of thousands of console
    // writes a minute and a measurable frame cost.
    {
      selector: "node[pill]",
      style: {
        "text-background-color": "data(pill)",
        // A tint, not a block. Alpha baked into the colour is ignored here.
        "text-background-opacity": 0.22,
      },
    },
    // Sized only where a size exists. A compound folder has none, and setting
    // width on it at all would override Cytoscape's auto-fit and collapse the
    // box to the 30px default instead of wrapping its contents.
    { selector: "node[size]", style: { width: "data(size)", height: "data(size)" } },
    // Only the handful of nodes worth naming at rest.
    { selector: "node[?named]", style: { label: "data(label)" } },
    // Folder boxes. Barely-there fills: they are meant to be read as regions
    // of the canvas, not as cards stacked on top of it.
    {
      selector: 'node[kind = "folder"]',
      style: {
        "background-color": "data(color)",
        "background-opacity": 0.09,
        shape: "round-rectangle",
        "border-width": 1,
        "border-color": "data(color)",
        "border-opacity": 0.35,
        padding: 16,
        label: "data(label)",
        "font-size": 10,
        "font-family": "DM Sans, sans-serif",
        color: INK.soft,
        "text-valign": "top",
        "text-halign": "center",
        "text-margin-y": -5,
        "text-background-opacity": 0,
        "min-zoomed-font-size": 6,
        events: "no",
      },
    },
    // A folder must never dim or light up with its contents -- the box is
    // scenery, and a selected file inside it should not make it glow.
    { selector: 'node[kind = "folder"].dim', style: { opacity: 0.35 } },
    {
      selector: "edge",
      style: {
        width: 1,
        // Not --line: a hairline that close to the canvas colour disappears
        // entirely at 1px. This is roughly where the reference sits, a warm
        // grey held back by opacity rather than by being nearly black.
        "line-color": "#6a655c",
        "line-style": "dashed",
        "line-dash-pattern": [3, 5],
        "target-arrow-shape": "none",
        "curve-style": "bezier",
        opacity: 0.5,
        "transition-property": "opacity, width, line-color",
        "transition-duration": 180,
      },
    },
    // Direction only becomes worth drawing once an edge is in focus; arrows on
    // every edge at rest is the noise the reference design leaves out.
    {
      selector: "edge.lit",
      style: {
        width: 1.4,
        "line-color": "data(color)",
        "line-style": "solid",
        "target-arrow-shape": "triangle",
        "target-arrow-color": "data(color)",
        "arrow-scale": 0.7,
        opacity: 1,
        "z-index": 20,
      },
    },
    {
      selector: "node.lit",
      style: { label: "data(label)", opacity: 1, "z-index": 30 },
    },
    {
      selector: "node.hover",
      style: { label: "data(label)", opacity: 1, "z-index": 50 },
    },
    {
      selector: "node.pick",
      style: {
        label: "data(label)",
        "border-width": 6,
        "border-color": "data(color)",
        "border-opacity": 0.28,
        "font-weight": 500,
        "z-index": 40,
      },
    },
    { selector: ".dim", style: { opacity: 0.12, "text-opacity": 0 } },
    { selector: "edge.dim", style: { opacity: 0.05 } },
    // Entrance: nodes are added invisible and faded in on a stagger.
    { selector: ".enter", style: { opacity: 0 } },
  ] as StylesheetJson;
}

/**
 * One layout, no picker. A force layout is the only arrangement that makes a
 * dependency graph legible to someone who has never seen the codebase, and
 * asking them to choose between four algorithm names is exactly the kind of
 * decision this interface should be making for them.
 */
export function layoutOptions(nodeCount: number): LayoutOptions {
  return {
    name: "fcose",
    quality: nodeCount > 800 ? "draft" : "default",
    randomize: true,
    animate: false,
    fit: true,
    padding: 70,
    nodeRepulsion: 9000,
    idealEdgeLength: 110,
    nodeSeparation: 140,
    packComponents: true,
    // Hold each folder's contents together and keep the boxes off each other.
    gravityRangeCompound: 1.2,
    gravityCompound: 1.4,
    nestingFactor: 0.2,
  } as unknown as LayoutOptions;
}
