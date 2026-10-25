// Thumbnails tijdens snel scrollen: een nieuwe <img> (laden, decoderen, tekenen) kost op een
// trage processor meer dan de rest van een rij bij elkaar. Zolang er gescrold wordt, krijgt per
// stap maar één wachtende thumbnail toestemming om te laden, de nieuwste eerst (die staat het
// langst in beeld); de rest ziet even een leeg vlak. Een stap is één frame zolang de frames op
// tijd komen, en STEP_SLOW_MS als ze dat niet doen (een trage processor). Staat het scrollen
// stil (IDLE_MS), dan mogen ze allemaal. Een adres dat al geladen werd, mag altijd meteen.

const IDLE_MS = 120;
/** Een frame dat later komt dan dit, telt als traag */
const LATE_FRAME_MS = 25;
/** Na een traag frame zo lang de langzame stap */
const SLOW_FOR_MS = 3000;
const STEP_SLOW_MS = 66;
/** Zoveel adressen onthouden als "al geladen" */
const KEEP = 4000;

const granted = new Set<string>();
const waiting = new Set<Waiter>();
let lastScroll = -Infinity;
let frame: number | null = null;
let lastFrame = 0;
let lastGrant = 0;
let slowUntil = 0;

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

/**
 * Eén lus per frame, zolang er gescrold wordt of iemand wacht: houdt bij of de frames op tijd
 * komen, en geeft er één toestemming per frame (of per STEP_SLOW_MS als ze dat niet doen).
 */
function tick(at: number): void {
  frame = null;
  const busy = scrolling();
  if (!busy && !waiting.size) {
    lastFrame = 0; // de lus stopt: de pauze telt straks niet als een traag frame
    return;
  }
  if (lastFrame && at - lastFrame > LATE_FRAME_MS) slowUntil = at + SLOW_FOR_MS;
  lastFrame = at;
  if (!busy) {
    grantAll();
  } else if (waiting.size && (at >= slowUntil || at - lastGrant >= STEP_SLOW_MS)) {
    // De nieuwste wachtende (achteraan in de Set)
    let last: Waiter | null = null;
    for (const w of waiting) last = w;
    if (last) {
      waiting.delete(last);
      remember(last.url);
      lastGrant = at;
      last.grant();
    }
  }
  schedule();
}

function schedule(): void {
  if (frame !== null) return;
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
      lastScroll = now();
      schedule();
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
  if (frame !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
  frame = null;
  lastFrame = lastGrant = slowUntil = 0;
}

/** Voor tests: doen alsof er net gescrold is. */
export function scrolledForTests(at = now()): void {
  lastScroll = at;
}
