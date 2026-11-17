// Wat de pagina /zoeken bij de treffers zegt (fase 6, Z4; Keuze 9, 20 en 47): de snippet van een
// treffer (gemarkeerde woorden als <mark>, altijd als tekst, nooit als HTML) en de meldingen
// (de index is nog bezig, de rondes van Keuze 9, niets gevonden). notices() is los van React te
// testen; Notices zet ze in één role="status", zodat een schermlezer ze hoort.

import { Fragment, type JSX } from "react";
import { type MessageKey, t } from "../i18n";
import type { IndexProgress, SnippetPart } from "./types";

/** De snippet van een treffer: tekst, met de woorden uit de vraag in <mark>. React zet elk stuk
 * als tekst in de DOM; HTML in een stuk blijft dus tekst. */
export function Snippet({ parts }: { parts: readonly SnippetPart[] }): JSX.Element {
  return (
    <span className="snippet">
      {parts.map((p, i) =>
        p.m ? <mark key={i}>{p.t}</mark> : <Fragment key={i}>{p.t}</Fragment>,
      )}
    </span>
  );
}

/** Wat de pagina tot nu toe weet: van alle geladen pagina's samen. */
export interface ResultState {
  /** Zoveel treffers staan er */
  count: number;
  /** Een geladen pagina raakte de tijdsgrens (ronde B of C van Keuze 9) */
  partial: boolean;
  /** Een geladen pagina kwam uit ronde C: alleen namen */
  namesOnly: boolean;
  /** De backfill van de tekst (Keuze 20), van de laatste pagina */
  indexing: IndexProgress | null;
}

export interface Notice {
  key: MessageKey;
  vars?: Record<string, number>;
}

/** De meldingen, in deze volgorde: de index, dan wat de treffers zijn (of dat er geen zijn). */
export function notices(s: ResultState): Notice[] {
  const out: Notice[] = [];
  // Naar beneden afgerond: 99,6% is nog niet klaar
  if (s.indexing) out.push({ key: "search.indexing", vars: { pct: Math.floor(s.indexing.done * 100) } });
  if (s.partial) {
    out.push({ key: s.count > 0 ? "search.partial" : "search.partialNone" });
    if (s.namesOnly && s.count > 0) out.push({ key: "search.namesOnly" });
  } else if (s.count === 0) {
    out.push({ key: "search.none" });
  }
  return out;
}

/** De meldingen in één role="status" (ook leeg: dan hoort een schermlezer de volgende). */
export function Notices({ list }: { list: readonly Notice[] }): JSX.Element {
  return (
    <div role="status" className="search-notices">
      {list.map((n) => (
        <p key={n.key} className="muted">
          {t(n.key, n.vars)}
        </p>
      ))}
    </div>
  );
}
