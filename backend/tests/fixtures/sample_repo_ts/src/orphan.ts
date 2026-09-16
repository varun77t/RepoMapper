import { slugify } from "./lib/format";

export function neverUsed(x: string): string {
  return slugify(x);
}
