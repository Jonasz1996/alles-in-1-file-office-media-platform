// "Filter in deze map": alleen wat er al geladen is, op naam. Elk woord moet erin staan;
// hoofdletters en accenten tellen niet (zoals zoeken op de server).

import type { FileNode } from "../api";
import { fold } from "../i18n";
import { searchName } from "./sort";

/** De woorden van een filter, zonder hoofdletters en accenten; leeg = geen filter. */
export function filterWords(text: string): string[] {
  return fold(text).split(/\s+/).filter(Boolean);
}

export function matches(node: FileNode, words: readonly string[]): boolean {
  if (words.length === 0) return true;
  const name = searchName(node);
  return words.every((w) => name.includes(w));
}

/** De ids (in hun volgorde) waarvan de node bij het filter past. */
export function filterIds(ids: readonly string[], nodes: ReadonlyMap<string, FileNode>, text: string): string[] {
  const words = filterWords(text);
  if (words.length === 0) return [...ids];
  return ids.filter((id) => {
    const node = nodes.get(id);
    return node !== undefined && matches(node, words);
  });
}
