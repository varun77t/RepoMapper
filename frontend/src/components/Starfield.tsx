import { useEffect, useRef } from "react";

import { INK } from "../lib/palette";

/**
 * The drifting field behind the landing screen.
 *
 * Hand-drawn on a canvas rather than run through Cytoscape: there is no data
 * here, only motion, and sixty free-floating dots with a few dashed links cost
 * almost nothing to draw directly. Honours prefers-reduced-motion by painting
 * one still frame.
 *
 * Dots orbit the centre rather than drifting in straight lines. Linear drift
 * needs edge wrapping, which pulls the field apart into an even scatter within
 * a minute; slow orbits hold the constellation together indefinitely.
 */

interface Dot {
  angle: number;
  radius: number;
  /** Radians per frame -- signed, so the field counter-rotates against itself. */
  spin: number;
  /** Phase and amplitude of the slow in-and-out breath. */
  phase: number;
  breath: number;
  r: number;
  color: string;
  x: number;
  y: number;
  label?: string;
}

const ACCENTS = ["#d9603b", "#7c5cd6", "#c9b98f", "#e0a33e", "#5fa39b"];
const LABELS = ["routes", "models", "helpers"];

const DOT_COUNT = 58;

/**
 * Links are a fixed handful of chosen pairs, not every pair within a radius.
 * Distance-based linking turns into a mesh the moment the window narrows, and
 * a mesh is the opposite of what this should feel like -- the reference has a
 * few deliberate lines across a lot of empty space.
 */
const LINK_COUNT = 9;

export function Starfield() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0;
    let height = 0;
    let dots: Dot[] = [];
    let links: [number, number][] = [];
    let ticks = 0;

    const seed = () => {
      const span = Math.min(width, height);
      dots = Array.from({ length: DOT_COUNT }, (_, index) => {
        // Roughly one dot in six carries colour; the rest stay near-neutral so
        // the colour reads as accent rather than confetti.
        const accented = index % 6 === 0;
        return {
          // sqrt keeps the dots evenly spread over the disc instead of piling
          // up at the centre.
          angle: Math.random() * Math.PI * 2,
          radius: span * (0.12 + 0.42 * Math.sqrt(Math.random())),
          spin: (Math.random() - 0.5) * 0.00075,
          phase: Math.random() * Math.PI * 2,
          breath: 0.02 + Math.random() * 0.05,
          r: accented ? 2.6 : 1.6 + Math.random() * 1.1,
          color: accented ? ACCENTS[((index / 6) | 0) % ACCENTS.length] : INK.dim,
          x: 0,
          y: 0,
        };
      });
      // Pills ride the middle band. Further out they orbit off the edge of a
      // narrow window; further in they cross the headline.
      const band = dots.filter(
        (dot) => dot.radius > span * 0.26 && dot.radius < span * 0.44,
      );
      LABELS.forEach((label, index) => {
        const pick = band[Math.floor((index / LABELS.length) * band.length)];
        if (pick) {
          pick.label = label;
          pick.color = ACCENTS[index % ACCENTS.length];
          pick.r = 3;
        }
      });

      links = Array.from({ length: LINK_COUNT }, () => [
        Math.floor(Math.random() * dots.length),
        Math.floor(Math.random() * dots.length),
      ]).filter(([a, b]) => a !== b) as [number, number][];
    };

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      seed();
    };

    const place = () => {
      const cx = width / 2;
      const cy = height / 2;
      // Stretch the orbits along the window's long axis so the field fills a
      // wide monitor and a narrow one equally, without ever throwing a pill
      // past the edge.
      const span = Math.min(width, height);
      const stretchX = (width / span) * 0.86;
      const stretchY = (height / span) * 0.86;
      for (const dot of dots) {
        const breath = 1 + Math.sin(ticks * 0.004 + dot.phase) * dot.breath;
        dot.x = cx + Math.cos(dot.angle) * dot.radius * breath * stretchX;
        dot.y = cy + Math.sin(dot.angle) * dot.radius * breath * stretchY;
      }
    };

    /**
     * The field has to part around the headline and the input, or a pill
     * eventually drifts behind the word "RepoMap" and reads as a bug. Rather
     * than forbidding the centre -- which leaves a visible hole -- everything
     * fades out as it approaches the text and back in as it leaves.
     */
    const clarity = (x: number, y: number): number => {
      const rx = Math.min(width * 0.44, 330);
      const ry = 165;
      const d = Math.hypot((x - width / 2) / rx, (y - height / 2) / ry);
      if (d >= 1.35) return 1;
      if (d <= 0.95) return 0;
      return (d - 0.95) / 0.4;
    };

    const drawPill = (dot: Dot) => {
      context.font = "500 11px 'DM Sans', system-ui, sans-serif";
      const text = dot.label as string;
      const padX = 9;
      const h = 22;
      const x = dot.x + 9;
      const y = dot.y - h / 2;
      const w = context.measureText(text).width + padX * 2 + 14;

      context.globalAlpha = clarity(dot.x, dot.y);
      context.beginPath();
      context.roundRect(x, y, w, h, h / 2);
      context.fillStyle = `${dot.color}2e`;
      context.fill();

      context.beginPath();
      context.arc(x + padX + 1, dot.y, 4, 0, Math.PI * 2);
      context.fillStyle = dot.color;
      context.fill();

      context.fillStyle = INK.text;
      context.textBaseline = "middle";
      context.fillText(text, x + padX + 11, dot.y + 0.5);
      context.globalAlpha = 1;
    };

    const draw = () => {
      context.clearRect(0, 0, width, height);
      const fade = Math.hypot(width, height) * 0.55;

      context.setLineDash([3, 5]);
      context.lineWidth = 1;
      context.strokeStyle = "rgba(164, 159, 149, 0.22)";
      for (const [a, b] of links) {
        const distance = Math.hypot(dots[a].x - dots[b].x, dots[a].y - dots[b].y);
        // Midpoint is enough: a link only crosses the text when its middle does.
        const clear = clarity(
          (dots[a].x + dots[b].x) / 2,
          (dots[a].y + dots[b].y) / 2,
        );
        context.globalAlpha = Math.max(0, 1 - distance / fade) * clear;
        context.beginPath();
        context.moveTo(dots[a].x, dots[a].y);
        context.lineTo(dots[b].x, dots[b].y);
        context.stroke();
      }
      context.globalAlpha = 1;
      context.setLineDash([]);

      for (const dot of dots) {
        context.beginPath();
        context.arc(dot.x, dot.y, dot.r, 0, Math.PI * 2);
        context.fillStyle = dot.color;
        context.globalAlpha = (dot.label ? 1 : 0.8) * clarity(dot.x, dot.y);
        context.fill();
      }
      context.globalAlpha = 1;

      for (const dot of dots) if (dot.label) drawPill(dot);
    };

    const step = () => {
      ticks++;
      for (const dot of dots) dot.angle += dot.spin;
      place();
      draw();
      frame = requestAnimationFrame(step);
    };

    let frame = 0;
    resize();
    place();
    if (reduced) {
      draw();
    } else {
      frame = requestAnimationFrame(step);
    }

    const observer = new ResizeObserver(() => {
      resize();
      place();
      if (reduced) draw();
    });
    observer.observe(canvas);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  return <canvas className="starfield" ref={canvasRef} aria-hidden />;
}
