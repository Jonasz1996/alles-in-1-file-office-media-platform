// Sneltoetsen. Eén keydown-luisteraar voor de hele app, met een stapel scopes: de bovenste
// scope krijgt de toets eerst; een blokkerende scope (een dialoog of menu) houdt alles eronder
// tegen. Nooit Ctrl+N/T/W of Ctrl+Shift+N: die zijn van de browser.

import { type KeyboardEvent as ReactKeyboardEvent, useEffect, useRef } from "react";

/** "mod+a", "shift+f10", "delete", "?", "arrowdown", "g". mod = Cmd op een Mac, anders Ctrl. */
export type Combo = string;

/** Scopes die alles eronder tegenhouden. */
const BLOCKING = new Set(["dialog", "menu"]);

export function isMacPlatform(): boolean {
  const nav = globalThis.navigator as (Navigator & { userAgentData?: { platform?: string } }) | undefined;
  const platform = nav?.userAgentData?.platform ?? nav?.platform ?? "";
  return /mac|iphone|ipad|ipod/i.test(platform);
}

interface ParsedCombo {
  key: string;
  mod: boolean;
  ctrl: boolean;
  meta: boolean;
  alt: boolean;
  shift: boolean;
}

const ALIASES: Record<string, string> = {
  esc: "escape",
  del: "delete",
  space: " ",
  spatie: " ",
  up: "arrowup",
  down: "arrowdown",
  left: "arrowleft",
  right: "arrowright",
  plus: "+",
  return: "enter",
};

function parse(combo: Combo): ParsedCombo {
  const parts = combo.toLowerCase().split("+");
  // "mod++" of "+" zelf: de laatste lege delen zijn de plus-toets
  let key = parts.pop() ?? "";
  if (key === "" && parts.length && parts[parts.length - 1] === "") {
    parts.pop();
    key = "+";
  }
  const set = new Set(parts);
  return {
    key: ALIASES[key] ?? key,
    mod: set.has("mod"),
    ctrl: set.has("ctrl"),
    meta: set.has("meta") || set.has("cmd"),
    alt: set.has("alt") || set.has("option"),
    shift: set.has("shift"),
  };
}

type KeyLike = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey"> & { code?: string };

/** Hoort deze toets bij deze combinatie? Bij één leesteken zonder letter (zoals "?" of "+")
 * telt Shift niet mee: die is op elk toetsenbord anders nodig. */
export function matchKey(e: KeyLike, c: Combo, isMac: boolean = isMacPlatform()): boolean {
  const p = parse(c);
  const key = (e.key ?? "").toLowerCase();
  const wantCtrl = p.ctrl || (p.mod && !isMac);
  const wantMeta = p.meta || (p.mod && isMac);
  if (e.ctrlKey !== wantCtrl || e.metaKey !== wantMeta || e.altKey !== p.alt) return false;
  const symbol = p.key.length === 1 && !/[a-z0-9]/.test(p.key);
  if (!symbol && e.shiftKey !== p.shift) return false;
  if (key === p.key) return true;
  // Is key zelf al een gewone letter of cijfer, dan geldt die (AZERTY: Ctrl+Q is fysiek KeyA en
  // mag geen "mod+a" zijn)
  if (/^[a-z0-9]$/.test(key)) return false;
  // Met Alt (Mac) of een niet-Latijns toetsenbord geeft key een ander teken: dan de fysieke toets
  if (p.key.length === 1 && /[a-z]/.test(p.key) && e.code === `Key${p.key.toUpperCase()}`) return true;
  if (p.key.length === 1 && /[0-9]/.test(p.key) && e.code === `Digit${p.key}`) return true;
  return false;
}

const MAC_SYMBOLS: Record<string, string> = { mod: "⌘", meta: "⌘", cmd: "⌘", ctrl: "⌃", alt: "⌥", shift: "⇧" };
const NAMES: Record<string, string> = {
  arrowup: "↑",
  arrowdown: "↓",
  arrowleft: "←",
  arrowright: "→",
  escape: "Esc",
  delete: "Delete",
  backspace: "Backspace",
  enter: "Enter",
  " ": "Space",
  pageup: "PgUp",
  pagedown: "PgDn",
  home: "Home",
  end: "End",
  tab: "Tab",
};

function keyLabel(key: string, isMac: boolean): string {
  if (isMac && key === "backspace") return "⌫";
  if (NAMES[key]) return NAMES[key];
  if (/^f\d{1,2}$/.test(key)) return key.toUpperCase();
  return key.length === 1 ? key.toUpperCase() : key.charAt(0).toUpperCase() + key.slice(1);
}

/** Voor in een menu of de hulp: "Ctrl+Shift+A", op een Mac "⇧⌘A". */
export function formatCombo(c: Combo, isMac: boolean = isMacPlatform()): string {
  const p = parse(c);
  const named = keyLabel(p.key, isMac);
  if (isMac) {
    const mods = [p.ctrl && "ctrl", p.alt && "alt", p.shift && "shift", (p.mod || p.meta) && "mod"].filter(
      Boolean,
    ) as string[];
    return mods.map((m) => MAC_SYMBOLS[m]).join("") + named;
  }
  const mods = [(p.mod || p.ctrl) && "Ctrl", p.meta && "Win", p.alt && "Alt", p.shift && "Shift"].filter(
    Boolean,
  ) as string[];
  return [...mods, named].join("+");
}

/** Typt iemand ergens (veld, keuzelijst, bewerkbare tekst)? Dan geen sneltoetsen van één letter. */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!t || typeof t !== "object") return false;
  const el = t as { tagName?: string; type?: string; isContentEditable?: boolean };
  if (el.isContentEditable) return true;
  const tag = (el.tagName ?? "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const type = (el.type ?? "text").toLowerCase();
  return !["checkbox", "radio", "button", "submit", "reset", "range", "color", "file", "image"].includes(type);
}

const NAV_KEYS = new Set(["arrowup", "arrowdown", "arrowleft", "arrowright", "home", "end", "pageup", "pagedown"]);
const ACTIVATE_KEYS = new Set([" ", "enter"]);
/** Invoertypes die de pijltjes, Home/End en PageUp/Down zelf gebruiken. */
const NAV_INPUTS = new Set(["range", "radio", "number", "date", "time", "datetime-local", "month", "week"]);
/** Invoertypes die Enter of Spatie zelf gebruiken (aanvinken, klikken). */
const ACTIVATE_INPUTS = new Set(["checkbox", "radio", "button", "submit", "reset", "file", "color", "image"]);

/**
 * Gebruikt het element met de focus deze toets zelf (de standaardactie van de browser)? Een
 * schuif, keuzerondjes, een cijferveld en een video of audio met bediening gebruiken de
 * pijltjes, Home/End en PageUp/Down; knoppen, links, summary en vinkjes Enter en Spatie. Dan
 * neemt een sneltoets zonder Ctrl/Cmd hem niet af (geen preventDefault). Eigen widgets met een
 * ARIA-rol (grid, slider, tabs) handelen hun toetsen zelf af met preventDefault; dat respecteert
 * dispatch al.
 */
export function consumesKey(
  t: EventTarget | null,
  e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey">,
): boolean {
  if (!t || typeof t !== "object" || e.ctrlKey || e.metaKey) return false;
  const key = (e.key ?? "").toLowerCase();
  const nav = NAV_KEYS.has(key);
  const activate = ACTIVATE_KEYS.has(key);
  if (!nav && !activate) return false;
  const el = t as { tagName?: string; type?: string; hasAttribute?: (name: string) => boolean };
  const tag = (el.tagName ?? "").toUpperCase();
  const has = (name: string) => el.hasAttribute?.(name) ?? false;
  if (tag === "SELECT") return true;
  if ((tag === "VIDEO" || tag === "AUDIO") && has("controls")) return true;
  if (tag === "INPUT") {
    const type = (el.type ?? "text").toLowerCase();
    return nav ? NAV_INPUTS.has(type) : ACTIVATE_INPUTS.has(type);
  }
  if (activate) return tag === "BUTTON" || tag === "SUMMARY" || (tag === "A" && has("href"));
  return false;
}

export interface Shortcut {
  combo: Combo | Combo[];
  run(e: KeyboardEvent): void;
  /** Voor de hulp (?); zonder label staat hij er niet in. */
  label?: string;
  when?(): boolean;
  /** Ook als de focus in een invoerveld staat (bijv. Escape). */
  allowInInput?: boolean;
}

interface Registration {
  scope: string;
  bindings: () => readonly Shortcut[];
  order: number;
}

/** Het register achter useShortcuts; los te testen zonder DOM. */
export class ShortcutRegistry {
  private stack: string[] = ["global"];
  private regs = new Set<Registration>();
  private order = 0;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly isMac: () => boolean = isMacPlatform) {}

  /** Zet een scope bovenop (bijv. "dialog" als een dialoog opent); geeft de functie om hem weg te halen. */
  pushScope(name: string): () => void {
    const marker = name;
    this.stack.push(marker);
    this.changed();
    let popped = false;
    return () => {
      if (popped) return;
      popped = true;
      // Van boven af zoeken: een dialoog in een dialoog sluit in de goede volgorde
      const i = this.stack.lastIndexOf(marker);
      if (i > 0) this.stack.splice(i, 1);
      this.changed();
    };
  }

  scopes(): readonly string[] {
    return this.stack;
  }

  register(scope: string, bindings: () => readonly Shortcut[]): () => void {
    const reg: Registration = { scope, bindings, order: ++this.order };
    this.regs.add(reg);
    this.changed();
    return () => {
      this.regs.delete(reg);
      this.changed();
    };
  }

  subscribe(cb: () => void): () => void {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  }

  private changed(): void {
    for (const l of this.listeners) l();
  }

  /** De scopes die nu toetsen krijgen: van boven tot en met de eerste blokkerende. ignore: die
   * scopes overslaan alsof ze er niet zijn (ook niet blokkerend). */
  private reachable(ignore: readonly string[] = []): string[] {
    const out: string[] = [];
    for (let i = this.stack.length - 1; i >= 0; i--) {
      const scope = this.stack[i];
      if (ignore.includes(scope)) continue;
      if (!out.includes(scope)) out.push(scope);
      if (BLOCKING.has(scope)) break;
    }
    return out;
  }

  private bindingsIn(scope: string): Shortcut[] {
    return [...this.regs]
      .filter((r) => r.scope === scope)
      .sort((a, b) => b.order - a.order) // de nieuwste eerst
      .flatMap((r) => r.bindings());
  }

  /** Een toets afhandelen; true als een sneltoets hem nam (dan preventDefault). */
  dispatch(e: KeyboardEvent): boolean {
    if (e.isComposing || e.defaultPrevented) return false;
    const typing = isTypingTarget(e.target);
    const consumed = consumesKey(e.target, e);
    const mac = this.isMac();
    for (const scope of this.reachable()) {
      for (const b of this.bindingsIn(scope)) {
        if ((typing || consumed) && !b.allowInInput) continue;
        const combos = Array.isArray(b.combo) ? b.combo : [b.combo];
        if (!combos.some((c) => matchKey(e, c, mac))) continue;
        if (b.when && !b.when()) continue;
        e.preventDefault();
        b.run(e);
        return true;
      }
    }
    return false;
  }

  /** Voor de hulp: wat er nu werkt, met label. ignore: scopes die er zo meteen niet meer zijn
   * (het menu waaruit de hulp werd geopend). */
  active(o?: { ignore?: readonly string[] }): { scope: string; combo: Combo; label: string }[] {
    const out: { scope: string; combo: Combo; label: string }[] = [];
    for (const scope of this.reachable(o?.ignore)) {
      for (const b of this.bindingsIn(scope)) {
        if (!b.label || (b.when && !b.when())) continue;
        const combos = Array.isArray(b.combo) ? b.combo : [b.combo];
        out.push({ scope, combo: combos[0], label: b.label });
      }
    }
    return out;
  }
}

export const shortcuts = new ShortcutRegistry();

let installed = false;
function install(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.addEventListener("keydown", (e) => {
    shortcuts.dispatch(e);
  });
}

export function pushScope(name: string): () => void {
  return shortcuts.pushScope(name);
}

export function activeShortcuts(o?: { ignore?: readonly string[] }): { scope: string; combo: Combo; label: string }[] {
  return shortcuts.active(o);
}

/** Zet een scope bovenop zolang het component er staat (een pagina: "browser"; Dialog doet
 * "dialog" zelf). */
export function useScope(name: string, enabled = true): void {
  useEffect(() => {
    if (!enabled) return;
    install();
    return shortcuts.pushScope(name);
  }, [name, enabled]);
}

/**
 * Sneltoetsen zolang het component er staat. scope: standaard "global" (altijd actief, behalve
 * onder een dialoog of menu). Een andere scope werkt alleen als die op de stapel staat
 * (useScope/pushScope); een dialoog of menu bovenop houdt alles eronder tegen.
 */
export function useShortcuts(b: Shortcut[], o?: { scope?: string; enabled?: boolean }): void {
  const latest = useRef(b);
  useEffect(() => {
    latest.current = b;
  });
  const scope = o?.scope ?? "global";
  const enabled = o?.enabled ?? true;
  useEffect(() => {
    if (!enabled) return;
    install();
    return shortcuts.register(scope, () => latest.current);
  }, [scope, enabled]);
}

// --- bewegen door een lijst of raster ---

export type Move = "up" | "down" | "left" | "right" | "home" | "end" | "pageup" | "pagedown";

/** De volgende plek in een lijst of raster (columns > 1); blijft binnen 0..count-1. */
export function moveIndex(cur: number, m: Move, count: number, columns = 1, pageRows = 10): number {
  if (count <= 0) return -1;
  const cols = Math.max(1, Math.floor(columns));
  const last = count - 1;
  // Nog niets met focus: elke beweging begint bovenaan, End onderaan
  if (cur < 0 || cur > last) return m === "end" ? last : 0;
  switch (m) {
    case "home":
      return 0;
    case "end":
      return last;
    case "left":
      return cols === 1 ? cur : Math.max(0, cur - 1);
    case "right":
      return cols === 1 ? cur : Math.min(last, cur + 1);
    case "up":
      return cur - cols >= 0 ? cur - cols : cur;
    case "down":
      if (cur + cols <= last) return cur + cols;
      // De laatste rij van een raster is korter: dan naar de laatste cel
      return Math.floor(cur / cols) < Math.floor(last / cols) ? last : cur;
    case "pageup": {
      const n = cur - pageRows * cols;
      return n >= 0 ? n : cur % cols;
    }
    case "pagedown": {
      const n = cur + pageRows * cols;
      return n <= last ? n : last;
    }
  }
}

/** Welke beweging een toets is (zonder modifiers behalve Shift voor selecteren). */
export function keyToMove(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey">): Move | null {
  if (e.altKey) return null;
  switch (e.key) {
    case "ArrowUp":
      return "up";
    case "ArrowDown":
      return "down";
    case "ArrowLeft":
      return "left";
    case "ArrowRight":
      return "right";
    case "Home":
      return "home";
    case "End":
      return "end";
    case "PageUp":
      return "pageup";
    case "PageDown":
      return "pagedown";
    default:
      return null;
  }
}

/** Een keydown-handler voor een element (useRovingFocus). */
export type KeyHandler = (e: ReactKeyboardEvent<HTMLElement>) => void;
