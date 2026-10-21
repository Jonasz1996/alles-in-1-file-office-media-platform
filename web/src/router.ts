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
  // Na het event: de luisteraar hierboven zette het op "pop"
  lastNavigation = opts?.replace ? "replace" : "push";
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
const scrolls = new Map<string, number>();

function saveScroll(): void {
  const s = historyState();
  if (!s) return;
  scrolls.set(s.key, window.scrollY);
  try {
    const all = Object.fromEntries([...scrolls].slice(-50)); // hooguit 50 bewaren
    sessionStorage.setItem(SCROLL_KEY, JSON.stringify(all));
  } catch {
    /* opslag uit: alleen in het geheugen */
  }
}

function savedScroll(key: string): number | undefined {
  if (scrolls.has(key)) return scrolls.get(key);
  try {
    const all = JSON.parse(sessionStorage.getItem(SCROLL_KEY) ?? "{}") as Record<string, number>;
    return typeof all[key] === "number" ? all[key] : undefined;
  } catch {
    return undefined;
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", saveScroll);
  try {
    window.history.scrollRestoration = "manual";
  } catch {
    /* niet ondersteund */
  }
}

/**
 * Zet de scrollpositie terug als je met terug/vooruit (of na herladen) op deze pagina komt, en
 * begint bovenaan na een gewone navigatie. ready: de inhoud staat er (anders is de pagina nog
 * te kort om naar de oude plek te scrollen). Alleen een ander pad telt: een andere query
 * (?bestand= van de viewer, een filter) laat de pagina staan waar hij was.
 */
export function useRestoreScroll(ready: boolean): void {
  const { path } = useLocation();
  useEffect(() => {
    if (!ready) return;
    const s = ensureState();
    if (lastNavigation === "push") {
      window.scrollTo(0, 0);
      return;
    }
    const y = savedScroll(s.key);
    if (y !== undefined) window.scrollTo(0, y);
  }, [ready, path]);
  useEffect(() => {
    const onScroll = () => {
      const s = historyState();
      if (s) scrolls.set(s.key, window.scrollY);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
}

// --- welk onderdeel van de navigatie gemarkeerd is ---

export type NavSection = "files" | "photos" | "shared" | "trash" | "admin" | "account";

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
