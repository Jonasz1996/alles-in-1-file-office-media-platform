// De toetsen van de speler (Keuze 33) als pure tabel. Op /kijken krijgt de speler ze allemaal
// (scope "player"); in de viewer van fase 3 alleen Spatie, K, J, L, M, C, A, P en 0–9 (scope
// "viewer"): ←, →, Home, End, F, I, D, E en ? blijven daar van de viewer, en N bestaat er niet.
// "?" op /kijken is de lijst van de app zelf (ShortcutHelp, scope global): de speler zet hem
// niet zelf. Nooit Ctrl+N/T/W: die zijn van de browser.

import type { Combo, Shortcut } from "../ui/keys";

export type PlayerAction =
  | "play"
  | "back10"
  | "forward10"
  | "back5"
  | "forward5"
  | "volumeUp"
  | "volumeDown"
  | "mute"
  | "fullscreen"
  | "subtitles"
  | "audio"
  | "pip"
  | "prevChapter"
  | "nextChapter"
  | "percent"
  | "begin"
  | "end"
  | "next"
  | "info"
  | "escape";

export const DIGITS: readonly Combo[] = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];

/** De tabel van Keuze 33, in de volgorde van de hulp. */
export const KEYMAP: Readonly<Record<PlayerAction, readonly Combo[]>> = {
  play: [" ", "k"],
  back10: ["j"],
  forward10: ["l"],
  back5: ["arrowleft"],
  forward5: ["arrowright"],
  volumeUp: ["arrowup"],
  volumeDown: ["arrowdown"],
  mute: ["m"],
  fullscreen: ["f"],
  subtitles: ["c"],
  audio: ["a"],
  pip: ["p"],
  prevChapter: ["pageup"],
  nextChapter: ["pagedown"],
  percent: DIGITS,
  begin: ["home"],
  end: ["end"],
  next: ["n"],
  info: ["i"],
  escape: ["escape"],
};

export const ACTIONS = Object.keys(KEYMAP) as PlayerAction[];

/** Wat de speler in de viewer krijgt (Keuze 33, review R19). */
export const VIEWER_ACTIONS: ReadonlySet<PlayerAction> = new Set<PlayerAction>([
  "play",
  "back10",
  "forward10",
  "mute",
  "subtitles",
  "audio",
  "pip",
  "percent",
]);

export type Where = "watch" | "viewer";

export function actionsFor(where: Where): PlayerAction[] {
  return where === "watch" ? ACTIONS : ACTIONS.filter((a) => VIEWER_ACTIONS.has(a));
}

/** De scope van de toetsen: op /kijken een eigen scope bovenop global, in de viewer die van de
 * viewer (de dialoog eronder houdt global daar tegen). */
export function scopeFor(where: Where): "player" | "viewer" {
  return where === "watch" ? "player" : "viewer";
}

/** Het cijfer van een toets 0–9 (ook op een toetsenbord waar key iets anders geeft). */
export function digitOf(e: Pick<KeyboardEvent, "key"> & { code?: string }): number | null {
  if (/^[0-9]$/.test(e.key)) return Number(e.key);
  const m = /^(?:Digit|Numpad)([0-9])$/.exec(e.code ?? "");
  return m ? Number(m[1]) : null;
}

/** Naar welke tijd een cijfer springt: 0–90 % van de duur. */
export function percentTime(digit: number, duration: number): number {
  if (!Number.isFinite(duration) || duration <= 0) return 0;
  return (Math.max(0, Math.min(9, digit)) / 10) * duration;
}

/** De nieuwe tijd na een sprong, binnen 0..duur. */
export function seekBy(current: number, by: number, duration: number): number {
  const end = Number.isFinite(duration) && duration > 0 ? duration : Infinity;
  return Math.max(0, Math.min(end, current + by));
}

/** Het begin van het vorige of volgende hoofdstuk (in s), of null. Vorige: binnen 3 s na het
 * begin van een hoofdstuk gaat hij naar het hoofdstuk daarvoor, anders naar het begin van dit. */
export function chapterTarget(starts: readonly number[], t: number, dir: 1 | -1): number | null {
  const sorted = [...starts].sort((a, b) => a - b);
  if (dir === 1) return sorted.find((s) => s > t + 0.5) ?? null;
  const before = sorted.filter((s) => s <= t);
  if (!before.length) return null;
  const cur = before[before.length - 1];
  if (t - cur > 3) return cur;
  return before.length > 1 ? before[before.length - 2] : 0;
}

/** Het volume na ↑ of ↓: stappen van 10 %, binnen 0..1. */
export function volumeStep(volume: number, dir: 1 | -1): number {
  return Math.round(Math.max(0, Math.min(1, volume + dir * 0.1)) * 10) / 10;
}

export interface Handlers {
  run(action: PlayerAction, e: KeyboardEvent): void;
  /** Mag deze toets nu? (bijv. N alleen met een volgende aflevering) */
  can?(action: PlayerAction): boolean;
  label(action: PlayerAction): string;
}

/** De sneltoetsen voor useShortcuts. */
export function bindings(where: Where, h: Handlers): Shortcut[] {
  return actionsFor(where).map((action) => ({
    combo: [...KEYMAP[action]],
    run: (e: KeyboardEvent) => h.run(action, e),
    label: h.label(action),
    when: h.can ? () => h.can?.(action) ?? true : undefined,
  }));
}
