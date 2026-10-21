// Selecteren in een lijst of raster, zoals in een bestandsbeheerder: klik = alleen dit, Ctrl/Cmd
// = erbij of eraf, Shift = een reeks vanaf het anker. Pure functies plus een kleine store.

import { useSyncExternalStore } from "react";

export interface SelectionState {
  selected: ReadonlySet<string>;
  /** Waar een Shift-reeks begint */
  anchor: string | null;
  /** Het item met de focus (de "cursor") */
  focus: string | null;
}

export const EMPTY_SELECTION: SelectionState = { selected: new Set(), anchor: null, focus: null };

function range(order: readonly string[], from: string | null, to: string): string[] {
  const end = order.indexOf(to);
  if (end < 0) return [];
  const start = from === null ? -1 : order.indexOf(from);
  if (start < 0) return [to];
  const [a, b] = start <= end ? [start, end] : [end, start];
  return order.slice(a, b + 1);
}

/** Een klik (of tik) op id. toggle = Ctrl/Cmd ingedrukt, shift = Shift ingedrukt. */
export function clickSelect(
  s: SelectionState,
  order: readonly string[],
  id: string,
  m: { shift: boolean; toggle: boolean },
): SelectionState {
  if (m.shift) {
    const anchor = s.anchor !== null && order.includes(s.anchor) ? s.anchor : id;
    const reeks = range(order, anchor, id);
    const selected = new Set(m.toggle ? s.selected : []);
    for (const x of reeks) selected.add(x);
    return { selected, anchor, focus: id };
  }
  if (m.toggle) {
    const selected = new Set(s.selected);
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    return { selected, anchor: id, focus: id };
  }
  return { selected: new Set([id]), anchor: id, focus: id };
}

/**
 * Met de pijltjes naar toId. Zonder modifier: alleen dat item geselecteerd. toggle (Ctrl/Cmd):
 * alleen de focus gaat mee, de selectie blijft (Spatie selecteert dan). shift: de reeks vanaf het anker.
 */
export function keySelect(
  s: SelectionState,
  order: readonly string[],
  toId: string,
  m: { shift: boolean; toggle: boolean },
): SelectionState {
  if (m.shift) {
    const anchor = s.anchor !== null && order.includes(s.anchor) ? s.anchor : (s.focus ?? toId);
    const selected = new Set(m.toggle ? s.selected : []);
    for (const x of range(order, anchor, toId)) selected.add(x);
    return { selected, anchor, focus: toId };
  }
  if (m.toggle) return { selected: s.selected, anchor: s.anchor, focus: toId };
  return { selected: new Set([toId]), anchor: toId, focus: toId };
}

/** Een selectie die je met useSelection volgt; elke wijziging is een nieuw object. */
export class SelectionStore {
  private state: SelectionState = EMPTY_SELECTION;
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): SelectionState => this.state;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  set(s: SelectionState): void {
    if (s === this.state) return;
    this.state = s;
    for (const l of this.listeners) l();
  }

  clear(): void {
    if (this.state.selected.size === 0 && this.state.anchor === null) return;
    this.set({ selected: new Set(), anchor: null, focus: this.state.focus });
  }

  selectAll(order: readonly string[]): void {
    this.set({ selected: new Set(order), anchor: order[0] ?? null, focus: this.state.focus ?? order[0] ?? null });
  }

  toggle(id: string): void {
    const selected = new Set(this.state.selected);
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    this.set({ selected, anchor: id, focus: id });
  }

  has(id: string): boolean {
    return this.state.selected.has(id);
  }

  /** Haalt weg wat er niet meer is (verwijderd, verplaatst). */
  retain(ids: ReadonlySet<string>): void {
    const s = this.state;
    const kept = [...s.selected].filter((id) => ids.has(id));
    const anchor = s.anchor !== null && ids.has(s.anchor) ? s.anchor : null;
    const focus = s.focus !== null && ids.has(s.focus) ? s.focus : null;
    if (kept.length === s.selected.size && anchor === s.anchor && focus === s.focus) return;
    this.set({ selected: new Set(kept), anchor, focus });
  }
}

export function useSelection(store: SelectionStore): SelectionState {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}
