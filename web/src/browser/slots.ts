// Vaste plekken (slots) voor de rijen van een gevirtualiseerde lijst. Bij het scrollen krijgt een
// rij die erbij komt de plek van een rij die uit beeld ging: React werkt dan een bestaand element
// bij in plaats van er een weg te gooien en een nieuw te maken (dat scheelt veel bij een trage
// processor). Een rij die blijft, houdt zijn plek, dus zijn element: ook de rij met de focus
// als hij buiten beeld raakt (extra), zodat de focus er niet af valt, en een rij die tijdens een
// klik de focus krijgt (anders komt de klik bij een ander element aan). De volgorde in de DOM is
// die van de plekken, niet van de rijen (dus geen verplaatsingen); aria-rowindex zegt welke rij
// het is.

import { useDeferredValue } from "react";

export interface Slot {
  index: number;
  key: string;
}

export class SlotAssigner {
  private slotOf = new Map<number, number>();

  /**
   * De rijen start..end (zonder end) plus extra (de vastgehouden rij buiten het bereik) op hun
   * plek. Een rij die blijft, houdt zijn plek; een nieuwe rij krijgt de laagste vrije. Twee
   * keer met dezelfde invoer geeft hetzelfde (StrictMode).
   */
  assign(start: number, end: number, extra: number | null): Slot[] {
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
    let free = 0;
    for (const i of wanted) {
      if (next.has(i)) continue;
      while (used.has(free)) free++;
      next.set(i, free);
      used.add(free);
    }
    this.slotOf = next;
    return [...next]
      .sort((a, b) => a[1] - b[1])
      .map(([index, s]) => ({ index, key: `s${s}` }));
  }
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
