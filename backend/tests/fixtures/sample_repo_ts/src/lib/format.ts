export function slugify(text: string): string {
  return text.toLowerCase().replace(/\s+/g, "-");
}

export const shout = (text: string): string => slugify(text).toUpperCase();
