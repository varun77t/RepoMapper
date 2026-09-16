import { describe } from "./lib";
import { Widget } from "./components/Widget";
import { slugify } from "@/lib/format";

export function createApp(label: string) {
  const slug = slugify(label);
  return { widget: Widget, text: describe(slug) };
}
