// Syntaxkleuren voor grote tekst (boven 100 KB) in een eigen thread: de pagina blijft dan
// reageren. Krijgt {id, text, language}, stuurt {id, lines} terug.

import { highlight } from "./highlight";

interface Request {
  id: number;
  text: string;
  language: string | null;
}

self.onmessage = (e: MessageEvent<Request>) => {
  const { id, text, language } = e.data;
  self.postMessage({ id, lines: highlight(text, language) });
};
