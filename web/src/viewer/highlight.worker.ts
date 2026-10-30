// Syntaxkleuren voor grote tekst (boven 100 KB) in een eigen thread: de pagina blijft dan
// reageren. Krijgt {id, text, language}, stuurt {id, result} terug: de getypte arrays van
// highlight() zonder de tekst zelf (die heeft de pagina al), overgedragen in plaats van gekopieerd.

import { highlight } from "./highlight";

interface Request {
  id: number;
  text: string;
  language: string | null;
}

self.onmessage = (e: MessageEvent<Request>) => {
  const { id, text, language } = e.data;
  const h = highlight(text, language);
  const result = { lineStarts: h.lineStarts, lineCount: h.lineCount, starts: h.starts, ends: h.ends, types: h.types, count: h.count };
  self.postMessage({ id, result }, [h.lineStarts.buffer, h.starts.buffer, h.ends.buffer, h.types.buffer]);
};
