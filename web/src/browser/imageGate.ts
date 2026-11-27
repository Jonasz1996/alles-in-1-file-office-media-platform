// Thumbnails tijdens snel scrollen: een nieuwe <img> (laden, decoderen, tekenen) kost op een
// trage processor meer dan de rest van een rij bij elkaar. Zolang er gescrold wordt, mag er per
// PX_PER_THUMB gescrolde pixels één wachtende thumbnail laden (hooguit één per frame), de
// nieuwste eerst: die staat het langst in beeld. De rest ziet even een leeg vlak. Per afstand en
// niet per tijd: dan laadt een trage processor er niet meer dan een snelle. Staat het scrollen
// stil (IDLE_MS), dan mogen ze allemaal. Een adres dat al geladen werd, mag altijd meteen.

// Ruim: op een trage processor komen scroll-gebeurtenissen soms 100 ms of meer na elkaar
const IDLE_MS = 250;
const PX_PER_THUMB = 300;
/** Zoveel tegoed spaart het scrollen hooguit op */
const MAX_CREDIT = 3;
/** Zoveel adressen onthouden als "al geladen" */
const KEEP = 4000;

const granted = new Set<string>();
const waiting = new Set<Waiter>();
let lastScroll = -Infinity;
let lastY: number | null = null;
let credit = 0;
let frame: number | null = null;

interface Waiter {
  url: string;
  grant(): void;
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function scrolling(): boolean {
  return now() - lastScroll < IDLE_MS;
}

function remember(url: string): void {
  if (granted.size >= KEEP) granted.clear();
  granted.add(url);
}

function grantAll(): void {
  for (const w of [...waiting]) {
    waiting.delete(w);
    remember(w.url);
    w.grant();
  }
}

/** Eén lus per frame zolang iemand wacht: geeft toestemming als er tegoed is (of alles bij stilstand). */
function tick(): void {
  frame = null;
  if (!waiting.size) return;
  if (!scrolling()) {
    grantAll();
    return;
  }
  if (credit >= 1) {
    // De nieuwste wachtende (achteraan in de Set)
    let last: Waiter | null = null;
    for (const w of waiting) last = w;
    if (last) {
      credit -= 1;
      waiting.delete(last);
      remember(last.url);
      last.grant();
    }
  }
  schedule();
}

function schedule(): void {
  if (frame !== null || !waiting.size) return;
  if (typeof requestAnimationFrame !== "function") {
    grantAll();
    return;
  }
  frame = requestAnimationFrame(tick);
}

if (typeof window !== "undefined") {
  window.addEventListener(
    "scroll",
    () => {
      const at = now();
      const y = window.scrollY;
      if (lastY !== null && at - lastScroll < IDLE_MS) {
        credit = Math.min(MAX_CREDIT, credit + Math.abs(y - lastY) / PX_PER_THUMB);
      }
      lastY = y;
      lastScroll = at;
    },
    { capture: true, passive: true },
  );
}

/** Mag dit adres nu meteen? (Al eerder geladen, of er wordt niet gescrold.) */
export function mayLoad(url: string): boolean {
  if (granted.has(url)) return true;
  if (scrolling()) return false;
  remember(url);
  return true;
}

/** Wacht op toestemming; geeft een functie om het wachten op te geven (unmount, ander adres). */
export function waitToLoad(url: string, grant: () => void): () => void {
  const w: Waiter = { url, grant };
  waiting.add(w);
  schedule();
  return () => {
    waiting.delete(w);
  };
}

/** Voor tests: alles vergeten. */
export function resetImageGateForTests(): void {
  granted.clear();
  waiting.clear();
  lastScroll = -Infinity;
  lastY = null;
  credit = 0;
  if (frame !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
  frame = null;
}

/** Voor tests: doen alsof er net gescrold is. */
export function scrolledForTests(at = now()): void {
  lastScroll = at;
}
