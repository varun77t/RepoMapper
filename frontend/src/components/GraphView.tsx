import cytoscape, { type Core } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";

import type { ElementBundle } from "../lib/elements";
import { buildStylesheet, layoutOptions } from "../lib/graphStyle";

// A hot reload re-evaluates this module against a Cytoscape that already has
// fcose registered, and registering twice throws. A module-level flag would
// not help -- it is reset by the same re-evaluation -- so catch it instead.
try {
  cytoscape.use(fcose);
} catch {
  /* already registered */
}

/** Total time the entrance cascade takes, however many nodes there are. */
const ENTRANCE_MS = 750;

/** Above this, the dash animation costs more than it adds. */
const FLOW_EDGE_LIMIT = 420;

/**
 * Rotation writes every node position each frame, so it is capped. Beyond a
 * few hundred nodes the write itself, not the trigonometry, is what drops
 * frames.
 */
const ROTATE_NODE_LIMIT = 320;

/** Radians per millisecond -- a full turn takes about three minutes. */
const ROTATE_SPEED = 0.000035;

/**
 * Frame the graph with room to turn in.
 *
 * A tightly fitted bounding box clips at the corners as soon as it rotates,
 * so the view pulls back to roughly the circle that contains it.
 */
function frameGraph(cy: Core): void {
  if (cy.elements().empty()) return;
  cy.fit(cy.elements(), 60);
  cy.zoom(cy.zoom() * 0.72);
  cy.center();
}

interface Props {
  bundle: ElementBundle;
  selectedId: string | null;
  search: string;
  /** Node ids to bring forward, e.g. every file in one cluster. */
  highlight: string[] | null;
  /** Incremented to recentre the view on demand. */
  fitToken: number;
  onSelect: (id: string | null) => void;
}

export function GraphView({
  bundle,
  selectedId,
  search,
  highlight,
  fitToken,
  onSelect,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  // Held in a ref so the instance effect can stay [] -- rebuilding Cytoscape
  // because a callback identity changed would throw away the layout.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  // Whether the reader has panned or zoomed since the graph was laid out. The
  // container is often still at its pre-mount size when the layout runs, so a
  // later resize has to re-frame -- but only while the view is still the one
  // the app chose, never over navigation the reader did themselves.
  const userMoved = useRef(false);

  useEffect(() => {
    if (!container.current) return;

    const cy = cytoscape({
      container: container.current,
      style: buildStylesheet(),
      minZoom: 0.06,
      maxZoom: 3.5,
      wheelSensitivity: 0.18,
      textureOnViewport: true,
      hideEdgesOnViewport: true,
      motionBlur: false,
      pixelRatio: 1,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (event) => onSelectRef.current(event.target.id()));
    cy.on("tap", (event) => {
      if (event.target === cy) onSelectRef.current(null);
    });
    // Hover names a node without committing to it. Kept separate from the
    // selection classes so moving the mouse away cannot strip the label off a
    // node that is lit because something is selected.
    cy.on("mouseover", "node", (event) => event.target.addClass("hover"));
    cy.on("mouseout", "node", (event) => event.target.removeClass("hover"));
    // Gesture-specific events: plain "zoom"/"pan" also fire for the app's own
    // programmatic framing, which would immediately disable re-framing.
    cy.on("dragpan scrollzoom pinchzoom", () => {
      userMoved.current = true;
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Elements, layout, and the entrance cascade.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    cy.batch(() => {
      cy.elements().remove();
      cy.add(bundle.elements);
    });
    if (bundle.elements.length === 0) return;

    cy.elements().addClass("enter");
    cy.layout(layoutOptions(bundle.nodeCount)).run();
    userMoved.current = false;
    frameGraph(cy);

    // Nodes arrive largest first, so the shape of the codebase resolves out of
    // the dark rather than appearing all at once. One rAF drives the whole
    // cascade; a timeout per node would be thousands of timers on a big graph.
    const ordered = cy.nodes().sort((a, b) => b.data("size") - a.data("size"));
    const total = ordered.length;
    const start = performance.now();
    let revealed = 0;
    let frame = 0;

    const step = (now: number) => {
      const progress = Math.min(1, (now - start) / ENTRANCE_MS);
      const target = Math.ceil(progress * total);
      if (target > revealed) {
        cy.batch(() => {
          for (let i = revealed; i < target; i++) ordered[i].removeClass("enter");
        });
        revealed = target;
      }
      if (progress < 1) {
        frame = requestAnimationFrame(step);
      } else {
        cy.edges().removeClass("enter");
      }
    };
    frame = requestAnimationFrame(step);

    return () => cancelAnimationFrame(frame);
  }, [bundle]);

  // Dashes drift along the edges, so a still graph still feels alive. Applied
  // to the collection as one style bypass per frame rather than re-running the
  // stylesheet, and skipped entirely on graphs where it would cost too much.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || bundle.edgeCount === 0 || bundle.edgeCount > FLOW_EDGE_LIMIT) return;

    let frame = 0;
    let offset = 0;
    let lastPaint = 0;

    const drift = (now: number) => {
      // ~30fps is indistinguishable from 60 for a drifting dash and halves the
      // work.
      if (now - lastPaint > 33) {
        offset = (offset + 0.55) % 8;
        cy.edges().style("line-dash-offset", -offset);
        lastPaint = now;
      }
      frame = requestAnimationFrame(drift);
    };
    frame = requestAnimationFrame(drift);

    return () => cancelAnimationFrame(frame);
  }, [bundle]);

  /**
   * The whole field turns, slowly, the way the reference does.
   *
   * Positions are rotated about the centroid captured when the effect starts;
   * recomputing it each frame would let rounding drift the graph off screen.
   * Only leaf nodes are moved -- a compound folder follows its children on its
   * own, and rotating it as well would move it twice.
   *
   * It stops the moment the reader is doing something: a selection, a search
   * or a highlight all mean they are reading, and text that will not hold
   * still is worse than no motion at all.
   */
  useEffect(() => {
    const cy = cyRef.current;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const busy = Boolean(selectedId) || search.trim() !== "" || Boolean(highlight?.length);
    if (!cy || reduced || busy) return;
    if (bundle.nodeCount === 0 || bundle.nodeCount > ROTATE_NODE_LIMIT) return;

    const leaves = cy.nodes(":childless");
    if (leaves.empty()) return;
    // Turn about the point the view is centred on, which cy.center() takes
    // from every element including the folder boxes. Spinning about the leaf
    // centroid instead makes the graph orbit slightly off-axis and swing its
    // far side out of frame.
    const box = cy.elements().boundingBox();
    const cx = (box.x1 + box.x2) / 2;
    const cy0 = (box.y1 + box.y2) / 2;

    let frame = 0;
    let last = 0;
    let held = false;
    const hold = () => (held = true);
    const release = () => (held = false);
    cy.on("grab", hold);
    cy.on("free", release);

    const turn = (now: number) => {
      if (!last) last = now;
      const elapsed = now - last;
      // ~30fps: a slower turn than this reads as smooth anyway, and it halves
      // the position writes.
      if (elapsed > 33 && !held) {
        const theta = ROTATE_SPEED * elapsed;
        const cos = Math.cos(theta);
        const sin = Math.sin(theta);
        cy.batch(() => {
          leaves.positions((node) => {
            const { x, y } = node.position();
            const dx = x - cx;
            const dy = y - cy0;
            return { x: cx + dx * cos - dy * sin, y: cy0 + dx * sin + dy * cos };
          });
        });
        last = now;
      } else if (held) {
        last = now;
      }
      frame = requestAnimationFrame(turn);
    };
    frame = requestAnimationFrame(turn);

    return () => {
      cancelAnimationFrame(frame);
      cy.off("grab", hold);
      cy.off("free", release);
    };
  }, [bundle, selectedId, search, highlight]);

  // Focus is computed from selection, cluster highlight and search together:
  // separate effects each adding and removing `dim` would fight over it.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    cy.batch(() => {
      cy.elements().removeClass("dim lit pick");

      const term = search.trim().toLowerCase();
      if (term) {
        const matches = cy
          .nodes()
          .filter((node) => String(node.data("path") ?? "").toLowerCase().includes(term));
        cy.elements().addClass("dim");
        matches.removeClass("dim").addClass("lit");
      }

      if (highlight && highlight.length > 0) {
        const wanted = new Set(highlight);
        const members = cy.nodes().filter((node) => wanted.has(node.id()));
        cy.elements().addClass("dim");
        members.union(members.edgesWith(members)).removeClass("dim").addClass("lit");
      }

      if (selectedId) {
        const node = cy.$id(selectedId);
        if (node.nonempty()) {
          cy.elements().addClass("dim");
          node.closedNeighborhood().removeClass("dim").addClass("lit");
          node.addClass("pick");
        }
      }
    });
  }, [selectedId, search, highlight, bundle]);

  // Cytoscape caches its container size, so a panel opening would otherwise
  // leave the graph rendering at the old width.
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(() => {
      const cy = cyRef.current;
      if (!cy) return;
      cy.resize();
      if (!userMoved.current) frameGraph(cy);
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (fitToken === 0) return;
    const cy = cyRef.current;
    if (!cy) return;
    userMoved.current = false;
    frameGraph(cy);
  }, [fitToken]);

  // Bring a node picked from a side panel into view.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !selectedId) return;
    const node = cy.$id(selectedId);
    if (node.empty()) return;
    cy.animate(
      { center: { eles: node }, zoom: Math.max(cy.zoom(), 0.85) },
      { duration: 380, easing: "ease-out-cubic" },
    );
  }, [selectedId]);

  return <div className="graph-canvas" ref={container} />;
}
