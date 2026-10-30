// De voortgang live (fase 5, M7, Keuze 36). Luistert naar playback.progress op de SSE-stroom van
// de app (events.ts) en geeft de standen van andere tabbladen en apparaten door: de rijen Verder
// kijken en Volgende aflevering werken hun kaarten bij, en de speler springt naar de nieuwe
// positie als hetzelfde item hier gepauzeerd openstaat (player/progress.ts).
//
// Een tabblad negeert zijn eigen origin: wat het zelf meldde, weet het al. ORIGIN is per
// tabblad willekeurig (per keer dat de app laadt); player/progress.ts stuurt hem mee met elke PUT.
//
// De pure functies (progressOf, applyToCard, applyContinue, applyNextUp) staan los van React,
// zodat vitest ze kan testen; useLiveRow is de haak voor de rijen.

import { useEffect, useState } from "react";
import { liveEvents, type ServerEvent } from "../events";
import type { Card, PlaybackProgressEvent } from "./types";

/** Vanaf hier (ms, meer dan) staat iets in Verder kijken; zo rekent de server ook (Keuze 36). */
export const CONTINUE_MIN_MS = 60_000;
/** Hooguit zoveel kaarten in Verder kijken. */
export const CONTINUE_MAX = 20;

/** 32 hextekens uit crypto.getRandomValues (dat kan ook zonder https, anders dan randomUUID). */
export function makeOrigin(fill: (bytes: Uint8Array<ArrayBuffer>) => unknown = (b) => crypto.getRandomValues(b)): string {
  const bytes = new Uint8Array(16);
  fill(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** De origin van dit tabblad. */
export const ORIGIN = makeOrigin();

/** Het event als het van een ander tabblad of apparaat komt en klopt; anders null. */
export function progressOf(e: ServerEvent, own: string = ORIGIN): PlaybackProgressEvent | null {
  if (e.type !== "playback.progress") return null;
  const d = e.data;
  if (typeof d.node_id !== "string" || typeof d.origin !== "string" || d.origin === own) return null;
  if (typeof d.position_ms !== "number" || typeof d.duration_ms !== "number" || typeof d.watched !== "boolean") {
    return null;
  }
  return {
    node_id: d.node_id,
    position_ms: d.position_ms,
    duration_ms: d.duration_ms,
    watched: d.watched,
    origin: d.origin,
    updated_at: typeof d.updated_at === "string" ? d.updated_at : "",
  };
}

type Listener = (e: PlaybackProgressEvent) => void;
const listeners = new Set<Listener>();
let unsubscribe: (() => void) | null = null;

/** Roept fn aan bij elke stand van een ander tabblad of apparaat; geeft het afmelden terug. */
export function onProgress(fn: Listener): () => void {
  listeners.add(fn);
  unsubscribe ??= liveEvents.subscribe((event) => {
    const p = progressOf(event);
    if (!p) return;
    for (const l of [...listeners]) {
      try {
        l(p);
      } catch {
        /* één kapotte luisteraar houdt de rest niet tegen */
      }
    }
  });
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && unsubscribe) {
      unsubscribe();
      unsubscribe = null;
    }
  };
}

/** De kaart met de nieuwe stand (balkje en gezien), of dezelfde kaart als het een andere is. */
export function applyToCard(card: Card, e: PlaybackProgressEvent): Card {
  if (card.node_id !== e.node_id || card.kind === "show") return card;
  const progress = !e.watched && e.position_ms > 0 ? { position_ms: e.position_ms, duration_ms: e.duration_ms } : null;
  return { ...card, progress, watched: e.watched };
}

/**
 * Verder kijken na een stand: staat de kaart erin, dan bijgewerkt en vooraan (de nieuwste
 * eerst); gezien, of niet meer voorbij 60 s, dan eruit. Een item dat er nog niet in stond,
 * komt met het opnieuw laden van de start (de kaart heeft gegevens nodig die het event niet
 * heeft).
 */
export function applyContinue(cards: readonly Card[], e: PlaybackProgressEvent): Card[] {
  const at = cards.findIndex((c) => c.node_id === e.node_id && c.kind !== "show");
  if (at === -1) return cards as Card[];
  const rest = cards.filter((_, i) => i !== at);
  if (e.watched || !(e.position_ms > CONTINUE_MIN_MS)) return rest;
  return [applyToCard(cards[at], e), ...rest].slice(0, CONTINUE_MAX);
}

/** Volgende aflevering na een stand: gezien gaat eruit (de volgende komt met het opnieuw
 * laden), een begonnen aflevering krijgt zijn balkje. */
export function applyNextUp(cards: readonly Card[], e: PlaybackProgressEvent): Card[] {
  if (!cards.some((c) => c.node_id === e.node_id)) return cards as Card[];
  if (e.watched) return cards.filter((c) => c.node_id !== e.node_id);
  return cards.map((c) => applyToCard(c, e));
}

/**
 * De kaarten van een rij die live meebewegen: begint bij cards (van GET /media/home) en past
 * elke stand van een ander tabblad of apparaat meteen toe. Nieuwe kaarten van de server
 * (opnieuw geladen) vervangen alles.
 */
export function useLiveRow(
  cards: readonly Card[],
  apply: (cards: readonly Card[], e: PlaybackProgressEvent) => Card[],
): readonly Card[] {
  const [state, setState] = useState<{ from: readonly Card[]; cards: readonly Card[] }>({ from: cards, cards });
  const shown = state.from === cards ? state.cards : cards;
  if (state.from !== cards) setState({ from: cards, cards });
  useEffect(
    () =>
      onProgress((e) =>
        setState((s) => {
          const next = apply(s.cards, e);
          return next === s.cards ? s : { from: s.from, cards: next };
        }),
      ),
    [apply],
  );
  return shown;
}
