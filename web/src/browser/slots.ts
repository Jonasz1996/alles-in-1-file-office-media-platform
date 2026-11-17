// Vaste plekken (slots) voor de rijen van een gevirtualiseerde lijst. Bij het scrollen krijgt een
// rij die erbij komt de plek van een rij die uit beeld ging: React werkt dan een bestaand element
// bij in plaats van er een weg te gooien en een nieuw te maken (dat scheelt veel bij een trage
// processor). Een rij die blijft, houdt zijn plek, dus zijn element: ook de rij met de focus
// als hij buiten beeld raakt (keep), zodat de focus er niet af valt, en een rij die tijdens een
// klik de focus krijgt (anders komt de klik bij een ander element aan). De volgorde in de DOM is
// die van de plekken, niet van de rijen (dus geen verplaatsingen); aria-rowindex zegt welke rij
// het is.
//
// Met een budget komen er per keer hooguit zoveel nieuwe rijen bij: de rij met de focus eerst,
// dan die het dichtst bij het midden van het bereik (het beeld). Een grote sprong (PageDown, End,
// terug naar de focus) gaat dan in een paar frames in plaats van in één lange taak. Tot alles er
// is, blijven de oude rijen op hun plek staan (buiten beeld), zodat hun elementen in de volgende
// stap hergebruikt worden; pending zegt dat er nog een stap moet komen.
//
// De plekken gaan pas over als React de render ook tekent. Een render rekent ze uit vanaf wat er
// getekend staat (planSlots, zonder iets te veranderen) en useLayoutEffect legt ze vast. Een
// render met lage voorrang (het bereik, een volgende stap) kan React halverwege weggooien als er
// een toets komt; die laat zo niets achter. Toen de plekken al tijdens het renderen overgingen,
// kreeg een oude rij die nog in de DOM stond in zo'n weggegooide render al een andere plek: Home
// tijdens de stappen na End zette de focus op het element van die oude rij 0 (focusRowNow), en
// de render van Home gaf dat element daarna aan een andere rij. De focus stond dan op een andere
// rij, of op <body> als die rij later wegging, en niets zette hem terug.

import { startTransition, useDeferredValue, useEffect, useLayoutEffect, useRef, useState } from "react";

export interface Slot {
  index: number;
  key: string;
}

/** Rij → plek. */
export type SlotMap = ReadonlyMap<number, number>;

export interface SlotPlan {
  /** De rijen met hun sleutel, in de volgorde van de plekken */
  slots: Slot[];
  /** Er ontbreken nog rijen (het budget was op): na het tekenen nog een stap */
  pending: boolean;
  /** Rij → plek van deze render; wordt de nieuwe stand zodra hij getekend is */
  slotOf: SlotMap;
}

export const NO_SLOTS: SlotMap = new Map();

/**
 * De rijen start..end (zonder end) en keep (de rij met de focus, ook buiten het bereik) op hun
 * plek, vanaf drawn: de plekken zoals ze nu in de DOM staan. Verandert niets: dezelfde invoer
 * geeft hetzelfde (StrictMode, een render die React opnieuw begint). Een rij die blijft, houdt
 * zijn plek; een nieuwe rij krijgt de laagste vrije. Met een budget is elke getekende stap een
 * stap verder.
 */
export function planSlots(
  drawn: SlotMap,
  start: number,
  end: number,
  keep: number | null,
  budget = Infinity,
): SlotPlan {
  const wanted: number[] = [];
  for (let i = start; i < end; i++) wanted.push(i);
  if (keep !== null && (keep < start || keep >= end)) wanted.push(keep);
  const next = new Map<number, number>();
  const used = new Set<number>();
  for (const i of wanted) {
    const s = drawn.get(i);
    if (s !== undefined) {
      next.set(i, s);
      used.add(s);
    }
  }
  let missing = wanted.filter((i) => !next.has(i));
  const pending = missing.length > budget;
  if (pending) {
    // De rij met de focus eerst (ook binnen het bereik: een toets zet de focus erop zodra hij
    // er staat), dan wat het dichtst bij het midden (het beeld) staat
    const mid = (start + end - 1) / 2;
    const rank = (i: number) => (i === keep ? -1 : Math.abs(i - mid));
    missing = missing.sort((a, b) => rank(a) - rank(b)).slice(0, budget);
  }
  let free = 0;
  for (const i of missing) {
    while (used.has(free)) free++;
    next.set(i, free);
    used.add(free);
  }
  if (pending) {
    // Oude rijen waarvan de plek nog niet nodig was: laten staan voor de volgende stap
    for (const [i, s] of drawn) {
      if (!used.has(s) && !next.has(i)) {
        next.set(i, s);
        used.add(s);
      }
    }
  }
  const slots = [...next].sort((a, b) => a[1] - b[1]).map(([index, s]) => ({ index, key: `s${s}` }));
  return { slots, pending, slotOf: next };
}

/**
 * De rijen van deze render op hun plek (planSlots). De plekken worden pas de nieuwe stand als
 * React deze render tekent (useLayoutEffect); zolang er rijen ontbreken, komt er na het tekenen
 * nog een stap (useNextStep).
 */
export function useSlots(start: number, end: number, keep: number | null, budget: number): Slot[] {
  const drawn = useRef<SlotMap>(NO_SLOTS);
  const plan = planSlots(drawn.current, start, end, keep, budget);
  useLayoutEffect(() => {
    drawn.current = plan.slotOf;
  });
  useNextStep(plan.pending);
  return plan.slots;
}

/**
 * Nog een stap na het tekenen zolang pending (planSlots met een budget): elke stap in een
 * volgend frame, met lage voorrang (zoals het bereik zelf).
 */
function useNextStep(pending: boolean): void {
  const [, setStep] = useState(0);
  useEffect(() => {
    if (!pending) return;
    const frame = requestAnimationFrame(() => startTransition(() => setStep((n) => n + 1)));
    return () => cancelAnimationFrame(frame);
  });
}

/**
 * Het bereik dat getekend wordt, met lage voorrang (useDeferredValue): bij een grote sprong
 * (PageDown, End, terug naar de focus) rendert React de nieuwe rijen in stukjes die de
 * hoofdthread tussendoor vrijgeven, in plaats van in één lange taak. Tot dan staan de vorige
 * rijen er nog. keep (de rij met de focus) staat er altijd, ook buiten het bereik.
 */
export function useDeferredRange(
  start: number,
  end: number,
  count: number,
  keep: number | null,
): { start: number; end: number; keep: number | null } {
  const packed = useDeferredValue(`${start}:${end}`);
  const [s, e] = packed.split(":").map(Number);
  return { start: Math.min(s, count), end: Math.min(e, count), keep: keep !== null && keep < count ? keep : null };
}
