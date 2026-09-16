/**
 * A warm, analog palette.
 *
 * Deliberately not the violet-on-black that every generated dashboard reaches
 * for: the canvas is a warm near-black and the accents are pigments -- rust,
 * amber, sand, clay -- with a single dusty violet rather than a violet family.
 * Colours are muted enough that a hundred of them on one screen still reads as
 * quiet.
 */

export const INK = {
  canvas: "#17171a",
  surface: "#1e1e22",
  raised: "#26262b",
  line: "#2f2f35",
  text: "#e8e4dd",
  soft: "#a49f95",
  muted: "#78736b",
  dim: "#4a4740",
} as const;

/** Cluster colours, indexed modulo length. */
const CLUSTER = [
  "#d9603b", // rust
  "#7c5cd6", // violet
  "#c9b98f", // sand
  "#6f9c78", // sage
  "#e0a33e", // amber
  "#5b8bb5", // slate blue
  "#b5715e", // clay
  "#9c7cb8", // mauve
  "#8a9c5b", // olive
  "#cf8093", // rose
  "#5fa39b", // teal
  "#bf8a4a", // ochre
];

export function clusterColor(id: number | null | undefined): string {
  if (id === null || id === undefined) return INK.dim;
  return CLUSTER[Math.abs(id) % CLUSTER.length];
}

/**
 * Blend a colour toward the canvas.
 *
 * Used for the files that are not carrying much weight. Painting those flat
 * grey looked right on an 80-file repo with several obvious hubs, but on a
 * 40-file one almost nothing clears the threshold and the whole graph
 * disappears into the background. A muted version of the area's own colour
 * stays legible and still says which part of the tree a file belongs to.
 */
export function mute(hex: string, keep = 0.8): string {
  const value = parseInt(hex.slice(1), 16);
  const blend = (channel: number, floor: number) =>
    Math.round(channel * keep + floor * (1 - keep));
  const r = blend((value >> 16) & 255, 0x17);
  const g = blend((value >> 8) & 255, 0x17);
  const b = blend(value & 255, 0x1a);
  return `rgb(${r}, ${g}, ${b})`;
}
