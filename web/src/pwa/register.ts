// De service worker registreren (P1) en melden dat er een nieuwe versie klaarstaat.
//
// - Alleen in een productiebuild, als de browser het kan en in een secure context (het
//   https-domein of localhost, B28).
// - Pas na load, nadat de pagina zichtbaar klaar is (een eigen meetpunt app:ready:<pagina>, of
//   5 s na load) en in een rustig moment (requestIdleCallback, hooguit 2 s): de worker haalt
//   bij het installeren de schil op en mag niet met de eerste weergave concurreren.
// - Een nieuwe versie wacht tot de gebruiker "Vernieuwen" kiest (applyUpdate).
// - Een build met VITE_SW=off schrijft elke worker uit en gooit de caches weg (de noodknop).

const SW_URL = "/sw.js";
const READY_PREFIX = "app:ready:";
/** Zonder eigen meetpunt van de pagina (alleen "route" van de schil): zo lang na load. */
const READY_FALLBACK_MS = 5000;
const UPDATE_EVERY_MS = 60 * 60 * 1000;

let waiting: ServiceWorker | null = null;
const listeners = new Set<() => void>();

function setWaiting(w: ServiceWorker | null): void {
  if (waiting === w) return;
  waiting = w;
  for (const l of listeners) l();
}

/** Staat er een nieuwe versie klaar? */
export function updateReady(): boolean {
  return waiting !== null;
}

/** Melding als updateReady() verandert (voor useSyncExternalStore). */
export function subscribeUpdate(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

let reloading = false;

/** De nieuwe versie nemen: de wachtende worker activeren en de pagina herladen zodra hij de
 * pagina bedient. */
export function applyUpdate(): void {
  const w = waiting;
  if (!w || !("serviceWorker" in navigator)) {
    window.location.reload();
    return;
  }
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });
  w.postMessage({ type: "skip-waiting" });
}

function afterLoad(): Promise<void> {
  if (document.readyState === "complete") return Promise.resolve();
  return new Promise((resolve) => window.addEventListener("load", () => resolve(), { once: true }));
}

/** Een meetpunt van een pagina zelf (niet alleen de schil, "route"). */
function pageReady(): boolean {
  return performance
    .getEntriesByType("mark")
    .some((m) => m.name.startsWith(READY_PREFIX) && m.name !== `${READY_PREFIX}route`);
}

function afterPageReady(): Promise<void> {
  if (pageReady() || typeof PerformanceObserver === "undefined") return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      observer.disconnect();
      clearTimeout(timer);
      resolve();
    };
    const observer = new PerformanceObserver(() => {
      if (pageReady()) finish();
    });
    try {
      observer.observe({ type: "mark", buffered: true });
    } catch {
      /* geen PerformanceObserver voor marks: dan alleen de wachttijd */
    }
    const timer = setTimeout(finish, READY_FALLBACK_MS);
  });
}

function idle(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestIdleCallback === "function") requestIdleCallback(() => resolve(), { timeout: 2000 });
    else setTimeout(resolve, 200);
  });
}

function watch(reg: ServiceWorkerRegistration): void {
  const sw = navigator.serviceWorker;
  const track = (w: ServiceWorker | null) => {
    if (!w) return;
    const check = () => {
      // Alleen een update als er al een worker de pagina bedient; de eerste installatie niet
      if (w.state === "installed" && sw.controller) setWaiting(w);
      if (w.state === "redundant" && waiting === w) setWaiting(null);
    };
    w.addEventListener("statechange", check);
    check();
  };
  track(reg.waiting);
  track(reg.installing);
  reg.addEventListener("updatefound", () => track(reg.installing));
  const update = () => {
    reg.update().catch(() => undefined); // offline of de server is weg: later opnieuw
  };
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") update();
  });
  setInterval(update, UPDATE_EVERY_MS);
}

/** De noodknop: alle workers uitschrijven en hun caches weg. */
async function unregisterAll(): Promise<void> {
  const regs = await navigator.serviceWorker.getRegistrations();
  await Promise.all(regs.map((r) => r.unregister()));
  if (typeof caches !== "undefined") {
    for (const name of await caches.keys()) if (name.startsWith("platform-")) await caches.delete(name);
  }
}

export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || typeof window === "undefined") return;
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  if (import.meta.env.VITE_SW === "off") {
    void afterLoad().then(unregisterAll).catch(() => undefined);
    return;
  }
  void (async () => {
    await afterLoad();
    await afterPageReady();
    await idle();
    const reg = await navigator.serviceWorker.register(SW_URL, { scope: "/" });
    watch(reg);
  })().catch(() => undefined); // geen worker: de app werkt gewoon, alleen niet offline
}
