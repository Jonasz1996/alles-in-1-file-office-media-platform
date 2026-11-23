// De tv-stand (fase 11, stap 9): het platform in de app voor de LG webOS-tv (apps/webos), te
// bedienen met de afstandsbediening. De app opent /tv?app=webos; dan onthoudt dit toestel de
// tv-stand (localStorage), zet het de klasse "tv" op <html> (groter, duidelijke focus; tv.css) en
// vangt het de toetsen van de afstandsbediening op:
//
// - pijltjes: naar het dichtstbijzijnde element in die richting (spatial.ts); in een tekstveld
//   blijven ← en → van het veld;
// - OK (Enter): klikt zoals altijd; in de speler zonder focus op een knop: afspelen/pauzeren;
// - Terug (461): een open venster of menu dicht (Escape), anders een pagina terug; op de start
//   van media sluit het de app;
// - Afspelen/Pauze/Stop/Vooruit/Terugspoelen: de speler (de toetsen van player/keys.ts);
// - groen: ondertitels, geel: scherper maken (upscaling in de speler), blauw: info.
//
// In de speler blijven ← en → spoelen (5 s), tenzij er een knop van de speler focus heeft.
// /tv?uit=1 zet de tv-stand op dit toestel weer uit.

import { best, type Dir } from "./spatial";
import "./tv.css";

const KEY = "tv.v1";

const KEYS = {
  left: 37,
  up: 38,
  right: 39,
  down: 40,
  enter: 13,
  back: 461,
  play: 415,
  pause: 19,
  stop: 413,
  rewind: 412,
  forward: 417,
  red: 403,
  green: 404,
  yellow: 405,
  blue: 406,
} as const;

const DIRS: Record<number, Dir> = { 37: "left", 38: "up", 39: "right", 40: "down" };

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function stored(): boolean {
  try {
    return localStorage.getItem(KEY) !== null;
  } catch {
    return false;
  }
}

function store(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, "webos");
    else localStorage.removeItem(KEY);
  } catch {
    /* geen opslag: alleen voor deze keer */
  }
}

let active = false;

export function isTv(): boolean {
  return active;
}

function visible(el: Element): boolean {
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  if (el.closest("[inert], [aria-hidden='true']")) return false;
  const style = getComputedStyle(el);
  return style.visibility !== "hidden" && style.display !== "none";
}

/** Waarbinnen de focus mag: het bovenste open venster of menu, anders de hele pagina. */
function area(): ParentNode {
  const layers = document.querySelectorAll(
    "dialog[open], [role='dialog'][aria-modal='true'], [role='menu'], .player-menu",
  );
  for (let i = layers.length - 1; i >= 0; i--) {
    if (visible(layers[i])) return layers[i];
  }
  return document;
}

function candidates(root: ParentNode): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(visible);
}

function focus(el: HTMLElement): void {
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function first(): boolean {
  const root = area();
  const main = root === document ? document.querySelector("main") : null;
  const list = candidates(main ?? root);
  const pick = list[0] ?? candidates(root)[0];
  if (!pick) return false;
  focus(pick);
  return true;
}

function move(dir: Dir): boolean {
  const current = document.activeElement as HTMLElement | null;
  const root = area();
  if (!current || current === document.body || !(root as Node).contains(current) || !visible(current)) {
    return first();
  }
  const list = candidates(root).filter((el) => el !== current && !el.contains(current));
  const i = best(current.getBoundingClientRect(), list.map((el) => el.getBoundingClientRect()), dir);
  if (i < 0) return false;
  focus(list[i]);
  return true;
}

function isText(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLTextAreaElement) return true;
  if (el instanceof HTMLInputElement) {
    return !["button", "checkbox", "radio", "submit", "reset", "range", "file", "color"].includes(el.type);
  }
  return (el as HTMLElement).isContentEditable;
}

function inPlayer(): boolean {
  return location.pathname.startsWith("/kijken/");
}

/** Een toets doorgeven aan de sneltoetsen van de app (player/keys.ts luistert op window). */
function press(key: string): void {
  window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
}

function closeApp(): void {
  const w = window as unknown as { webOS?: { platformBack?: () => void } };
  if (w.webOS?.platformBack) w.webOS.platformBack();
  else window.close();
}

function back(): void {
  if (area() !== document) {
    const target = document.activeElement ?? document.body;
    target.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    return;
  }
  if (location.pathname === "/media" || location.pathname === "/tv") {
    closeApp();
    return;
  }
  history.back();
}

function onKey(e: KeyboardEvent): void {
  if (!e.isTrusted) return; // onze eigen press()
  const code = e.keyCode;
  const active = document.activeElement;
  const player = inPlayer();
  const playerControl =
    player && active instanceof HTMLElement && active.matches("button, input, select, a") && active.closest(".player") !== null;
  let handled = true;
  switch (code) {
    case KEYS.back:
      back();
      break;
    case KEYS.play:
    case KEYS.pause:
      if (player) press(" ");
      else handled = false;
      break;
    case KEYS.stop:
      if (player) history.back();
      else handled = false;
      break;
    case KEYS.rewind:
      if (player) press("j");
      else handled = false;
      break;
    case KEYS.forward:
      if (player) press("l");
      else handled = false;
      break;
    case KEYS.green:
      if (player) press("c");
      else handled = false;
      break;
    case KEYS.yellow:
      if (player) press("u");
      else handled = false;
      break;
    case KEYS.blue:
      if (player) press("i");
      else handled = false;
      break;
    case KEYS.enter:
      if (player && !playerControl && !isText(active)) press(" ");
      else handled = false;
      break;
    case KEYS.left:
    case KEYS.right:
    case KEYS.up:
    case KEYS.down: {
      const dir = DIRS[code];
      const horizontal = dir === "left" || dir === "right";
      if (horizontal && isText(active)) {
        handled = false;
        break;
      }
      if (active instanceof HTMLInputElement && active.type === "range" && horizontal) {
        handled = false; // de tijdbalk: ← en → spoelen
        break;
      }
      if (player && horizontal && !playerControl && area() === document) {
        handled = false; // de speler spoelt
        break;
      }
      // Lukt het niet (geen element in die richting), dan scrollt de pagina zoals altijd
      handled = move(dir);
      break;
    }
    default:
      handled = false;
  }
  if (handled) {
    e.preventDefault();
    e.stopPropagation();
  }
}

/** Bij de start van de app: de tv-stand aan als dit toestel hem onthoudt of de tv-app hem vraagt. */
export function initTv(): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(location.search);
  if (location.pathname === "/tv" && params.has("uit")) {
    store(false);
    return;
  }
  if (params.get("app") === "webos" || location.pathname === "/tv") store(true);
  if (!stored()) return;
  active = true;
  document.documentElement.classList.add("tv");
  window.addEventListener("keydown", onKey, true);
}
