// Weggaan uit de viewer met wijzigingen die nog niet zijn opgeslagen (tekst bewerken, 0.8.3):
// TextView zet een bewaker zolang er iets te verliezen is; de viewer vraagt het voor sluiten en
// voor het volgende bestand, en de pagina verlaten (herladen, een tab dicht) vraagt de browser
// zelf (beforeunload). Eén bewaker tegelijk: er is één viewer.

type Ask = () => Promise<boolean>;

let guard: (() => boolean) | null = null;

/** Zet de bewaker (dirty: zijn er wijzigingen?); geeft de functie om hem weer weg te halen. */
export function setLeaveGuard(dirty: () => boolean): () => void {
  guard = dirty;
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
    if (ok) run();
  });
}

/** beforeunload: de browser vraagt zelf of je weg wilt (de tekst kiest hij zelf). */
export function onBeforeUnload(e: Pick<BeforeUnloadEvent, "preventDefault">): void {
  if (hasUnsaved()) e.preventDefault();
}
