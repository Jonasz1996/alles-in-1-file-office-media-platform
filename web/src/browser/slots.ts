// Vaste plekken (slots) voor de rijen van een gevirtualiseerde lijst. Bij het scrollen krijgt een
// rij die erbij komt de plek van een rij die uit beeld ging: React werkt dan een bestaand element
// bij in plaats van er een weg te gooien en een nieuw te maken (dat scheelt veel bij een trage
// processor). De volgorde in de DOM is die van de plekken, niet van de rijen (dus geen
// verplaatsingen); aria-rowindex zegt welke rij het is. De rij met de focus heeft een eigen
// sleutel ("f"): die blijft hetzelfde element, ook buiten beeld, zodat de focus er niet af valt.

import { useDeferredValue } from "react";

export interface Slot {
  index: number;
  key: string;
}

export class SlotAssigner {
  private slotOf = new Map<number, number>();

  /**
   * De rijen start..end (zonder end) plus extra (de vastgehouden rij buiten beeld) op hun plek.
   * keep is de rij met de focus (of null). Een rij die blijft, houdt zijn plek; een nieuwe rij
   * krijgt de laagste vrije. Twee keer met dezelfde invoer geeft hetzelfde (StrictMode).
   */
  assign(start: number, end: number, extra: number | null, keep: number | null): Slot[] {
    const next = new Map<number, number>();
    const used = new Set<number>();
    for (let i = start; i < end; i++) {
      if (i === keep) continue;
      const s = this.slotOf.get(i);
      if (s !== undefined) {
        next.set(i, s);
        used.add(s);
      }
    }
    let free = 0;
    for (let i = start; i < end; i++) {
      if (i === keep || next.has(i)) continue;
      while (used.has(free)) free++;
      next.set(i, free);
      used.add(free);
    }
    this.slotOf = next;
    const out: Slot[] = [];
    for (const [index, s] of next) out.push({ index, key: `s${s}` });
    out.sort((a, b) => (next.get(a.index) ?? 0) - (next.get(b.index) ?? 0));
    const focused = keep !== null && keep >= start && keep < end ? keep : extra;
    if (focused !== null) out.push({ index: focused, key: "f" });
    return out;
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
