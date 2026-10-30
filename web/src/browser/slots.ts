// Vaste plekken (slots) voor de rijen van een gevirtualiseerde lijst. Bij het scrollen krijgt een
// rij die erbij komt de plek van een rij die uit beeld ging: React werkt dan een bestaand element
// bij in plaats van er een weg te gooien en een nieuw te maken (dat scheelt veel bij een trage
// processor). Een rij die blijft, houdt zijn plek, dus zijn element: ook de rij met de focus
// als hij buiten beeld raakt (extra), zodat de focus er niet af valt, en een rij die tijdens een
// klik de focus krijgt (anders komt de klik bij een ander element aan). De volgorde in de DOM is
// die van de plekken, niet van de rijen (dus geen verplaatsingen); aria-rowindex zegt welke rij
// het is.
//
// Met een budget komen er per keer hooguit zoveel nieuwe rijen bij, die het dichtst bij het
// midden van het bereik (het beeld) eerst: een grote sprong (PageDown, End, terug naar de focus)
// gaat dan in een paar frames in plaats van in één lange taak. Tot alles er is, blijven de oude
// rijen op hun plek staan (buiten beeld), zodat hun elementen in de volgende stap hergebruikt
// worden; pending zegt dat er nog een stap moet komen.

import { startTransition, useDeferredValue, useEffect, useState } from "react";

export interface Slot {
  index: number;
  key: string;
}

export class SlotAssigner {
  private slotOf = new Map<number, number>();
  /** Na de laatste assign: er ontbreken nog rijen (het budget was op) */
  pending = false;

  /**
   * De rijen start..end (zonder end) plus extra (de vastgehouden rij buiten het bereik) op hun
   * plek. Een rij die blijft, houdt zijn plek; een nieuwe rij krijgt de laagste vrije. Zonder
   * budget geeft twee keer met dezelfde invoer hetzelfde (StrictMode); met een budget is elke
   * aanroep een stap verder.
   */
  assign(start: number, end: number, extra: number | null, budget = Infinity): Slot[] {
    const wanted: number[] = [];
    for (let i = start; i < end; i++) wanted.push(i);
    if (extra !== null && (extra < start || extra >= end)) wanted.push(extra);
    const next = new Map<number, number>();
    const used = new Set<number>();
    for (const i of wanted) {
      const s = this.slotOf.get(i);
      if (s !== undefined) {
        next.set(i, s);
        used.add(s);
      }
    }
    let missing = wanted.filter((i) => !next.has(i));
    this.pending = missing.length > budget;
    if (this.pending) {
      // De vastgehouden rij eerst, dan wat het dichtst bij het midden (het beeld) staat
      const mid = (start + end - 1) / 2;
      const rank = (i: number) => (i === extra ? -1 : Math.abs(i - mid));
      missing = missing.sort((a, b) => rank(a) - rank(b)).slice(0, budget);
    }
    let free = 0;
    for (const i of missing) {
      while (used.has(free)) free++;
      next.set(i, free);
      used.add(free);
    }
    if (this.pending) {
      // Oude rijen waarvan de plek nog niet nodig was: laten staan voor de volgende stap
      for (const [i, s] of this.slotOf) {
        if (!used.has(s) && !next.has(i)) {
          next.set(i, s);
          used.add(s);
        }
      }
    }
    this.slotOf = next;
    return [...next]
      .sort((a, b) => a[1] - b[1])
      .map(([index, s]) => ({ index, key: `s${s}` }));
  }
}

/**
 * Nog een stap na het tekenen zolang pending (SlotAssigner met een budget): elke stap in een
 * volgend frame, met lage voorrang (zoals het bereik zelf).
 */
export function useNextStep(pending: boolean): void {
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
 * rijen er nog. keep (de rij met de focus) staat er altijd, als extra buiten het bereik.
 */
export function useDeferredRange(
  start: number,
  end: number,
  count: number,
  keep: number | null,
): { start: number; end: number; extra: number | null } {
  const packed = useDeferredValue(`${start}:${end}`);
  const [s, e] = packed.split(":").map(Number);
  const from = Math.min(s, count);
  const to = Math.min(e, count);
  const extra = keep !== null && keep < count && (keep < from || keep >= to) ? keep : null;
  return { start: from, end: to, extra };
}
