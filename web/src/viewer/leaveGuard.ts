// Weggaan uit de viewer met wijzigingen die nog niet zijn opgeslagen (tekst bewerken, 0.8.3):
// TextView zet een bewaker zolang er iets te verliezen is; de viewer vraagt het voor sluiten en
// voor het volgende bestand, en ook bij Terug of Vooruit in de browser (of terugvegen op een
// telefoon: backGuard). De pagina verlaten (herladen, een tab dicht) vraagt de browser zelf
// (beforeunload). Eén bewaker tegelijk: er is één viewer.

type Ask = () => Promise<boolean>;

let guard: (() => boolean) | null = null;
/** Er is al ja gezegd op "Wijzigingen weggooien?": backGuard vraagt het niet nog eens (sluiten
 * gaat daarna nog via Terug in de geschiedenis). Een nieuwe bewaker begint weer zonder. */
let released = false;

/** Zet de bewaker (dirty: zijn er wijzigingen?); geeft de functie om hem weer weg te halen. */
export function setLeaveGuard(dirty: () => boolean): () => void {
  guard = dirty;
  released = false;
  return () => {
    if (guard === dirty) guard = null;
  };
}

/** Zijn er nu wijzigingen die verloren gaan? */
export function hasUnsaved(): boolean {
  return guard?.() ?? false;
}

/** Mag de viewer weg? Zonder wijzigingen meteen ja (synchroon: run nu); anders eerst ask (de
 * dialoog "Wijzigingen weggooien?"), en run alleen bij ja. */
export function whenMayLeave(run: () => void, ask: Ask): void {
  if (!hasUnsaved()) {
    run();
    return;
  }
  void ask().then((ok) => {
    if (!ok) return;
    released = true;
    run();
  });
}

/** beforeunload: de browser vraagt zelf of je weg wilt (de tekst kiest hij zelf). */
export function onBeforeUnload(e: Pick<BeforeUnloadEvent, "preventDefault">): void {
  if (hasUnsaved()) e.preventDefault();
}

/** Wat backGuard van een popstate-event gebruikt. */
export interface PopLike {
  isTrusted: boolean;
  state: unknown;
  stopImmediatePropagation(): void;
}

export interface BackDeps {
  /** De plek in de geschiedenis van de router (history.state.idx), of null */
  index(state: unknown): number | null;
  /** history.go */
  go(delta: number): void;
  ask: Ask;
}

/** De plek van de router in een history-state (router.ts: {key, idx}). */
export function routerIndex(state: unknown): number | null {
  const idx = (state as { idx?: unknown } | null)?.idx;
  return typeof idx === "number" ? idx : null;
}

/**
 * Een luisteraar voor popstate op window, met capture: die komt vóór de router (en vóór
 * useViewerParam, die bij Terug ?bestand= weghaalt en zo de viewer sluit). Alleen een echte stap
 * van de browser (isTrusted): navigate() van de app stuurt zelf een popstate, en dat is nooit Terug.
 * Met wijzigingen: het event gaat niet verder (de app merkt niets), de stap wordt teruggedraaid
 * (history.go de andere kant op), en dan de vraag; bij ja nog eens dezelfde stap, nu zonder vraag.
 * current: de plek in de geschiedenis nu (bij het plaatsen van de luisteraar).
 */
export function backGuard(deps: BackDeps, current: number | null): (e: PopLike) => void {
  let here = current;
  let asking = false;
  /** De stap terug die we zelf deden: die komt als een echte popstate, en is geen nieuwe vraag */
  let restoring = false;
  return (e) => {
    const to = deps.index(e.state);
    const back = restoring && e.isTrusted;
    if (back) restoring = false;
    if (back || !e.isTrusted || released || !hasUnsaved()) {
      here = to;
      return;
    }
    e.stopImmediatePropagation();
    // Hoeveel stappen het was (Terug: 1); onbekend: één terug
    const delta = here !== null && to !== null && to !== here ? here - to : 1;
    restoring = true;
    deps.go(delta);
    if (asking) return; // de vraag staat er al
    asking = true;
    void deps.ask().then((ok) => {
      asking = false;
      if (!ok) return;
      released = true;
      deps.go(-delta);
    });
  };
}
