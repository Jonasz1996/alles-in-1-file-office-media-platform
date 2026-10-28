// Eigen router op de History-API: geen externe bibliotheek. Paden blijven Nederlands
// (/map/:id, /gedeeld, /prullenbak). De routetabel staat in routes.tsx, <Link> in links.ts.

import { type ComponentType, useCallback, useEffect, useSyncExternalStore } from "react";
import type { Area, MessageKey } from "./i18n/types";

export interface Location {
  path: string;
  /** Met het vraagteken, of "" */
  search: string;
  query: URLSearchParams;
}

/** Elke eigen history-entry krijgt een sleutel (voor de scrollpositie) en een volgnummer
 * (voor back(): is er binnen de app een stap terug?). */
interface HistoryState {
  key: string;
  idx: number;
}

let seq = 0;
function newKey(): string {
  return `${Date.now().toString(36)}-${(++seq).toString(36)}`;
}

function historyState(): HistoryState | null {
  const s = window.history.state as Partial<HistoryState> | null;
  return s && typeof s.key === "string" && typeof s.idx === "number" ? (s as HistoryState) : null;
}

/** De eerste entry van deze pagina krijgt ook een sleutel. */
function ensureState(): HistoryState {
  const s = historyState();
  if (s) return s;
  const fresh = { key: newKey(), idx: 0 };
  window.history.replaceState({ ...(window.history.state ?? {}), ...fresh }, "");
  return fresh;
}

const listeners = new Set<() => void>();
let lastHref = "";
let lastLocation: Location = { path: "/", search: "", query: new URLSearchParams() };

function notify(): void {
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) window.addEventListener("popstate", notify);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) window.removeEventListener("popstate", notify);
  };
}

/** De huidige plek; hetzelfde object zolang het adres niet verandert (voor useSyncExternalStore). */
export function currentLocation(): Location {
  const { pathname, search } = window.location;
  const href = pathname + search;
  if (href !== lastHref) {
    lastHref = href;
    lastLocation = { path: pathname, search, query: new URLSearchParams(search) };
  }
  return lastLocation;
}

/** Of navigeren een push was (nieuwe pagina, bovenaan beginnen) of terug/vooruit (scrollpositie
 * terugzetten). */
let lastNavigation: "push" | "replace" | "pop" = "pop";

/** Wat de laatste navigatie was; voor useRestoreScroll en de focus na navigeren. */
export function navigationKind(): "push" | "replace" | "pop" {
  return lastNavigation;
}

if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => {
    lastNavigation = "pop";
  });
}

/**
 * Naar een ander adres binnen de app. Vergelijkt pad, query en hash: hetzelfde adres doet niets.
 * Stuurt een popstate-event, zodat ook code die zelf op popstate let (en window.location leest)
 * het merkt.
 */
export function navigate(href: string, opts?: { replace?: boolean }): void {
  const url = new URL(href, window.location.href);
  if (url.origin !== window.location.origin) {
    window.location.assign(url.href);
    return;
  }
  const target = url.pathname + url.search + url.hash;
  const { pathname, search, hash } = window.location;
  if (target === pathname + search + hash) return;
  saveScroll();
  const cur = ensureState();
  if (opts?.replace) {
    window.history.replaceState({ key: newKey(), idx: cur.idx }, "", target);
    lastNavigation = "replace";
  } else {
    window.history.pushState({ key: newKey(), idx: cur.idx + 1 }, "", target);
    lastNavigation = "push";
  }
  window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
  // Na het event: de luisteraars zetten het op "pop" en de entry op niet-actief
  lastNavigation = opts?.replace ? "replace" : "push";
  scrolls.pushed((historyState() ?? ensureState()).key);
}

/** Eén stap terug binnen de app; was dit de eerste pagina (deep link), dan naar fallback. */
export function back(fallback: string): void {
  const s = historyState();
  if (s && s.idx > 0) window.history.back();
  else navigate(fallback, { replace: true });
}

export function useLocation(): Location {
  return useSyncExternalStore(subscribe, currentLocation, currentLocation);
}

/** Compat: alleen het pad. */
export function usePath(): string {
  return useLocation().path;
}

/** Eén queryparameter lezen en zetten (bijv. ?bestand=). null haalt hem weg. */
export function useQueryParam(
  name: string,
): [string | null, (v: string | null, o?: { replace?: boolean }) => void] {
  const { query } = useLocation();
  const value = query.get(name);
  const set = useCallback(
    (v: string | null, o?: { replace?: boolean }) => {
      const params = new URLSearchParams(window.location.search);
      if (v === null) params.delete(name);
      else params.set(name, v);
      const qs = params.toString();
      navigate(`${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`, o);
    },
    [name],
  );
  return [value, set];
}

/**
 * Past het pad bij het patroon? Geeft de parameters, of null. "/map/:id", "/beheer/:tab?"
 * (optioneel laatste deel), "*" (alles). Een slash op het eind maakt niet uit.
 */
export function match(pattern: string, path: string): Record<string, string> | null {
  if (pattern === "*") return {};
  const want = pattern.split("/").filter(Boolean);
  const got = path.split("/").filter(Boolean);
  const params: Record<string, string> = {};
  for (let i = 0; i < Math.max(want.length, got.length); i++) {
    const w = want[i];
    const g = got[i];
    if (w === undefined) return null; // het pad is langer
    if (w === "*") return params;
    if (w.startsWith(":")) {
      const optional = w.endsWith("?");
      const name = w.slice(1, optional ? -1 : undefined);
      if (g === undefined) {
        if (optional) continue;
        return null;
      }
      try {
        params[name] = decodeURIComponent(g);
      } catch {
        return null; // kapotte %-codering
      }
      continue;
    }
    if (g === undefined || w !== g) return null;
  }
  return params;
}

/** Een gewone klik met de linkerknop, zonder Ctrl, Cmd, Shift of Alt: die blijft in de app.
 * Middelklik en klikken met een modifier openen zoals de browser dat wil (nieuw tabblad). */
export function isPlainLeftClick(e: {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

// --- scrollpositie per history-entry ---

const SCROLL_KEY = "platform-scroll";
/** Hooguit zoveel posities bewaren (in sessionStorage). */
const SCROLL_KEEP = 50;
/** Zo lang blijft een terugzetten wachten tot de pagina lang genoeg is. */
const RESTORE_MS = 3000;

/**
 * De scrollposities per history-entry, zonder DOM (los te testen). Scroll-events tellen alleen
 * voor de *actieve* entry: na terug/vooruit is er even geen actieve, tot het terugzetten klaar
 * is. Zo overschrijft het inkorten van de pagina (de browser klemt scrollY) de bewaarde plek niet.
 */
export class ScrollMemory {
  private positions = new Map<string, number>();
  private active: string | null = null;
  private pending: { key: string; y: number } | null = null;

  constructor(
    private readonly load: () => Record<string, number> = () => ({}),
    private readonly store: (all: Record<string, number>) => void = () => {},
  ) {}

  /** De bewaarde plek van een entry (eerst het geheugen, dan de opslag). */
  get(key: string): number | undefined {
    if (this.positions.has(key)) return this.positions.get(key);
    try {
      const y = this.load()[key];
      return typeof y === "number" ? y : undefined;
    } catch {
      return undefined;
    }
  }

  /** Een scroll-event: telt alleen voor de actieve entry. */
  record(key: string, y: number): void {
    if (key === this.active) this.positions.set(key, y);
  }

  /** Weg van deze entry (navigeren, pagehide): de plek bewaren, ook in de opslag. Loopt het
   * terugzetten nog, dan blijft de oude plek staan. */
  leave(key: string, y: number): void {
    if (key === this.active) this.positions.set(key, y);
    this.persist();
  }

  /** Terug of vooruit: geen actieve entry tot het terugzetten klaar is. */
  popped(): void {
    this.active = null;
    this.pending = null;
  }

  /** Terug of vooruit binnen dezelfde pagina (alleen de query of hash anders, zoals de viewer
   * sluiten): niets terug te zetten, de pagina blijft staan en de entry telt meteen vanaf y. */
  stayed(key: string, y: number): void {
    this.active = key;
    this.pending = null;
    this.positions.set(key, y);
  }

  /** Een nieuwe of vervangen entry: die is meteen actief (begint bovenaan). */
  pushed(key: string): void {
    this.active = key;
    this.pending = null;
  }

  /** Begint het terugzetten voor deze entry: de plek om naartoe te scrollen, of undefined (niets
   * bewaard; de entry is dan meteen actief). */
  startRestore(key: string): number | undefined {
    if (this.pending?.key === key) return this.pending.y;
    if (this.active === key) return undefined; // al terug (bijv. StrictMode, tweede aanroep)
    const y = this.get(key);
    if (y === undefined) {
      this.active = key;
      return undefined;
    }
    this.pending = { key, y };
    return y;
  }

  /** Stoppen met terugzetten (de wachttijd is om, of de gebruiker doet zelf iets): vanaf nu
   * telt de entry weer. */
  finish(key: string, scrollY: number): void {
    if (this.pending?.key !== key) return;
    this.pending = null;
    this.active = key;
    this.positions.set(key, scrollY);
  }

  isPending(key: string): boolean {
    return this.pending?.key === key;
  }

  /** Naar de opslag: wat er al stond plus het geheugen, de laatste SCROLL_KEEP. */
  private persist(): void {
    let stored: Record<string, number> = {};
    try {
      stored = this.load();
    } catch {
      /* kapotte opslag: opnieuw beginnen */
    }
    const all = new Map(Object.entries(stored).filter(([, y]) => typeof y === "number"));
    for (const [k, y] of this.positions) {
      all.delete(k); // achteraan: het nieuwste
      all.set(k, y);
    }
    try {
      this.store(Object.fromEntries([...all].slice(-SCROLL_KEEP)));
    } catch {
      /* opslag uit: alleen in het geheugen */
    }
  }
}

const scrolls = new ScrollMemory(
  () => JSON.parse(sessionStorage.getItem(SCROLL_KEY) ?? "{}") as Record<string, number>,
  (all) => sessionStorage.setItem(SCROLL_KEY, JSON.stringify(all)),
);

function saveScroll(): void {
  const s = historyState();
  if (s) scrolls.leave(s.key, window.scrollY);
}

/** Het lopende terugzetten (wacht tot de pagina lang genoeg is); stop() ruimt het op. */
let restoring: { key: string; stop: () => void } | null = null;

function stopRestoring(): void {
  restoring?.stop();
  restoring = null;
}

/** Scrolt naar y en blijft dat doen terwijl de pagina van hoogte verandert (de data komt
 * binnen, een lege staat wordt een lange lijst), tot de gebruiker zelf iets doet of RESTORE_MS
 * voorbij is. Ook als y al bereikt is: de inhoud kan daarna nog wisselen. Zolang telt geen
 * scroll-event voor deze entry. */
function restoreTo(key: string, y: number): void {
  window.scrollTo(0, y);
  if (restoring?.key === key) return; // wacht al; de poging hierboven was genoeg
  stopRestoring();
  const done = () => {
    if (restoring?.key !== key) return;
    stopRestoring();
    scrolls.finish(key, window.scrollY);
  };
  const retry = () => {
    if (Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
  };
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(retry) : null;
  observer?.observe(document.body);
  observer?.observe(document.documentElement);
  const timer = window.setTimeout(done, RESTORE_MS);
  const user = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
  for (const type of user) window.addEventListener(type, done, { passive: true, capture: true });
  restoring = {
    key,
    stop: () => {
      observer?.disconnect();
      window.clearTimeout(timer);
      for (const type of user) window.removeEventListener(type, done, { capture: true });
    },
  };
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", saveScroll);
  // Terug of vooruit (ook het eigen event van navigate(); dat zet daarna zelf de entry actief).
  // De plek van de vorige entry staat dan al in het geheugen: elk scroll-event schreef hem weg.
  // Blijft het pad hetzelfde, dan draait useRestoreScroll niet (alleen een ander pad telt): de
  // entry moet dan hier al actief worden, anders telt er geen scroll meer tot het volgende pad.
  let poppedPath = window.location.pathname;
  window.addEventListener("popstate", () => {
    stopRestoring();
    const path = window.location.pathname;
    const s = historyState();
    if (path === poppedPath && s) scrolls.stayed(s.key, window.scrollY);
    else scrolls.popped();
    poppedPath = path;
  });
  window.addEventListener(
    "scroll",
    () => {
      const s = historyState();
      if (s) scrolls.record(s.key, window.scrollY);
    },
    { passive: true },
  );
  try {
    window.history.scrollRestoration = "manual";
  } catch {
    /* niet ondersteund */
  }
}

/**
 * Zet de scrollpositie terug als je met terug/vooruit (of na herladen) op deze pagina komt, en
 * begint bovenaan na een gewone navigatie. ready: de inhoud staat er. De schil roept dit al aan
 * voor elke route (ready = true); het terugzetten blijft dan 3 s de plek vasthouden terwijl de
 * pagina groeit (of tot de gebruiker zelf iets doet). Een pagina die zijn data later laadt, mag
 * het ook zelf aanroepen met ready: binnen die tijd wordt het dan nog eens gezet. Alleen een ander
 * pad telt: een andere query (?bestand= van de viewer, een filter) laat de pagina staan waar hij
 * was.
 */
export function useRestoreScroll(ready: boolean): void {
  const { path } = useLocation();
  useEffect(() => {
    if (!ready) return;
    const s = ensureState();
    if (lastNavigation === "push") {
      window.scrollTo(0, 0);
      scrolls.pushed(s.key);
      return;
    }
    const y = scrolls.startRestore(s.key);
    if (y !== undefined) restoreTo(s.key, y);
  }, [ready, path]);
}

// --- welk onderdeel van de navigatie gemarkeerd is ---

// Fase 5 (M0): media en music, de menu-items Media en Muziek
export type NavSection = "files" | "photos" | "media" | "music" | "shared" | "trash" | "admin" | "account";

const sectionListeners = new Set<() => void>();
let sectionOverride: NavSection | null = null;

function setSectionOverride(s: NavSection | null): void {
  if (s === sectionOverride) return;
  sectionOverride = s;
  for (const l of sectionListeners) l();
}

function subscribeSection(l: () => void): () => void {
  sectionListeners.add(l);
  return () => {
    sectionListeners.delete(l);
  };
}

/** De markering die een pagina zelf zette (of null); voor de balk bovenaan. */
export function useNavSectionOverride(): NavSection | null {
  return useSyncExternalStore(
    subscribeSection,
    () => sectionOverride,
    () => sectionOverride,
  );
}

/**
 * Een pagina overschrijft de markering in de navigatie zolang hij er staat, bijv. Bestanden in
 * een map die met je gedeeld is → "shared". null: de markering van de route zelf.
 */
export function useNavSection(section: NavSection | null): void {
  useEffect(() => {
    setSectionOverride(section);
    return () => setSectionOverride(null);
  }, [section]);
}

// --- de routetabel (routes.tsx) ---

export interface RouteProps {
  params: Record<string, string>;
}

export interface RouteDef {
  pattern: string;
  /** Laadt het chunk én de woordenboeken van areas (Promise.all). */
  load: () => Promise<{ default: ComponentType<RouteProps> }>;
  areas: readonly Area[];
  /** document.title en de aankondiging na navigeren */
  title: MessageKey;
  section: NavSection | null;
  /** Wie er mag komen; zonder roles iedereen die ingelogd is. */
  roles?: ("admin" | "user" | "guest")[];
  /** Al iets ophalen tijdens het opstarten (parallel met /auth/refresh), bijv. de map. */
  preload?: (p: Record<string, string>) => void;
}
