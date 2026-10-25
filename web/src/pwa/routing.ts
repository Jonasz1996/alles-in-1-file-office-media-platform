// Welke verzoeken de service worker afhandelt. Puur (geen DOM, geen caches), zodat het in vitest
// te testen is; sw.ts importeert alleen dit.
//
// - "shell": een navigatie naar een pagina van de app: eerst het netwerk, zonder netwerk de
//   index.html uit de cache.
// - "asset": een chunk onder /assets/ (de naam bevat zijn hash, dus nooit verouderd) of een vast
//   bestand van de schil (boot.js, manifest, iconen): eerst de cache.
// - null: niet aankomen. De API, /health, /.well-known, Range-verzoeken (video), alles behalve
//   GET, en alles van een andere origin gaat gewoon naar het netwerk.

export type Route = "shell" | "asset" | null;

export interface RequestLike {
  url: string;
  method: string;
  mode?: string;
  headers?: { get(name: string): string | null } | Record<string, string>;
}

/** Vaste bestanden van de schil (uit public/); de service worker heeft ze in de precache. */
const SHELL_FILES = /^\/(boot\.js|manifest\.webmanifest|favicon\.(svg|ico)|icons\/[\w.-]+)$/;

/** Paden die nooit door de service worker gaan, ook niet als navigatie. */
const NEVER = /^\/(api(\/|$)|health$|ready$|\.well-known\/|sw\.js$)/;

function header(headers: RequestLike["headers"], name: string): string | null {
  if (!headers) return null;
  if (typeof (headers as { get?: unknown }).get === "function") {
    return (headers as { get(name: string): string | null }).get(name);
  }
  const record = headers as Record<string, string>;
  for (const key of Object.keys(record)) if (key.toLowerCase() === name) return record[key];
  return null;
}

/** Heeft het laatste stuk van het pad een extensie ("/manifest.webmanifest", "/x.png")? */
function hasExtension(path: string): boolean {
  const last = path.slice(path.lastIndexOf("/") + 1);
  return /\.[A-Za-z0-9]+$/.test(last);
}

/**
 * Wat de service worker met dit verzoek doet. origin: die van de app (in de service worker
 * self.location.origin); zonder origin geldt die van het adres zelf.
 */
export function classify(request: RequestLike, origin?: string): Route {
  let url: URL;
  try {
    url = new URL(request.url, origin);
  } catch {
    return null;
  }
  if (origin !== undefined && url.origin !== new URL(origin).origin) return null;
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (request.method.toUpperCase() !== "GET") return null;
  if (header(request.headers, "range") !== null) return null;
  const path = url.pathname;
  if (NEVER.test(path)) return null;
  if (request.mode === "navigate") {
    // Een pagina van de app; een bestand dat je in een tabblad opent (/icons/x.png) niet
    return hasExtension(path) && !path.endsWith(".html") ? null : "shell";
  }
  if (path.startsWith("/assets/") && path.length > "/assets/".length) return "asset";
  if (SHELL_FILES.test(path)) return "asset";
  return null;
}
