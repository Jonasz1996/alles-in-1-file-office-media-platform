// De service worker (P1). vite.config.ts bouwt hem na de app als één IIFE-bestand (dist/sw.js)
// zonder imports, met de lijst van de schil, alle chunks van de build en een build-id erin.
//
// - Installeren: alleen de schil vooraf ophalen (index.html, het entry-chunk met zijn imports,
//   de css, de basiswoordenboeken, boot.js, manifest en iconen). Lazy chunks komen bij gebruik.
// - Navigaties: eerst het netwerk; zonder netwerk de index.html van deze versie.
// - /assets/* en de vaste bestanden: eerst de cache, anders het netwerk (en dan bewaren).
// - Nooit /api, /health, /.well-known, Range-verzoeken of iets anders dan GET (routing.ts).
// - Een nieuwe versie wacht tot de pagina "skip-waiting" stuurt (de knop "Vernieuwen").
// - VITE_SW=off bouwt de noodknop: die ruimt alles op en schrijft zichzelf uit.

import { classify } from "./routing";

declare const __PRECACHE__: string[];
declare const __ASSETS__: string[];
declare const __BUILD__: string;
declare const __KILL_SWITCH__: boolean;

const sw = self as unknown as ServiceWorkerGlobalScope;

const PREFIX = "platform-";
/** De schil van deze versie; een nieuwe versie krijgt een nieuwe cache. */
const SHELL_CACHE = `${PREFIX}shell-${__BUILD__}`;
/** Chunks die bij gebruik binnenkwamen. Blijft over versies heen; bij activeren gaat eruit wat
 * niet meer in de build zit. */
const ASSET_CACHE = `${PREFIX}assets-1`;
const INDEX = "/index.html";

async function precache(): Promise<void> {
  const cache = await caches.open(SHELL_CACHE);
  // Niet uit de HTTP-cache: index.html en boot.js moeten van deze versie zijn
  await cache.addAll(__PRECACHE__.map((url) => new Request(url, { cache: "no-cache" })));
}

async function cleanUp(): Promise<void> {
  const keep = new Set([SHELL_CACHE, ASSET_CACHE]);
  for (const name of await caches.keys()) {
    if (name.startsWith(PREFIX) && !keep.has(name)) await caches.delete(name);
  }
  const known = new Set(__ASSETS__);
  const assets = await caches.open(ASSET_CACHE);
  for (const request of await assets.keys()) {
    if (!known.has(new URL(request.url).pathname)) await assets.delete(request);
  }
}

/** De noodknop: alle caches weg, uitschrijven en open pagina's opnieuw laden (zonder worker). */
async function selfDestruct(): Promise<void> {
  for (const name of await caches.keys()) if (name.startsWith(PREFIX)) await caches.delete(name);
  await sw.registration.unregister();
  for (const client of await sw.clients.matchAll({ type: "window" })) {
    void (client as WindowClient).navigate(client.url).catch(() => undefined);
  }
}

sw.addEventListener("install", (event) => {
  if (__KILL_SWITCH__) {
    void sw.skipWaiting();
    return;
  }
  event.waitUntil(precache());
});

sw.addEventListener("activate", (event) => {
  if (__KILL_SWITCH__) {
    event.waitUntil(selfDestruct());
    return;
  }
  event.waitUntil(
    (async () => {
      await cleanUp();
      // Navigaties laden parallel met het opstarten van de worker
      if (sw.registration.navigationPreload) await sw.registration.navigationPreload.enable();
      // De eerste keer: de open pagina meteen bedienen (anders pas na herladen). Bij een update
      // komt activeren pas na "Vernieuwen", en dan herlaadt de pagina toch.
      await sw.clients.claim();
    })(),
  );
});

sw.addEventListener("message", (event) => {
  const data = event.data as { type?: string } | null;
  if (data?.type === "skip-waiting") void sw.skipWaiting();
  if (data?.type === "build") event.ports[0]?.postMessage({ build: __BUILD__, kill: __KILL_SWITCH__ });
});

/** Een server die er wel is maar de app niet kan leveren (proxy zonder backend). */
function unavailable(res: Response): boolean {
  return res.status >= 502 && res.status <= 504;
}

async function navigation(event: FetchEvent): Promise<Response> {
  try {
    const preloaded = (await event.preloadResponse) as Response | undefined;
    const res = preloaded ?? (await fetch(event.request));
    if (!unavailable(res)) return res;
    const cached = await caches.match(INDEX, { cacheName: SHELL_CACHE });
    return cached ?? res;
  } catch (e) {
    const cached = await caches.match(INDEX, { cacheName: SHELL_CACHE });
    if (cached) return cached;
    throw e;
  }
}

async function asset(request: Request): Promise<Response> {
  const cached = await caches.match(request);
  if (cached) return cached;
  const res = await fetch(request);
  // Alleen complete antwoorden van een chunk bewaren (geen 404 of 206)
  if (res.status === 200 && new URL(request.url).pathname.startsWith("/assets/")) {
    const copy = res.clone();
    void caches
      .open(ASSET_CACHE)
      .then((cache) => cache.put(request, copy))
      .catch(() => undefined);
  }
  return res;
}

sw.addEventListener("fetch", (event) => {
  if (__KILL_SWITCH__) return;
  const route = classify(event.request, sw.location.origin);
  if (route === "shell") event.respondWith(navigation(event));
  else if (route === "asset") event.respondWith(asset(event.request));
  // null: geen respondWith, de browser doet het zelf
});
