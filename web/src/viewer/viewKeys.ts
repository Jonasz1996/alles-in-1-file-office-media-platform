// De sleutels van de viewer (0.8.3, review txt punt 1). keyOf hoort bij de inhoud: een nieuwe
// etag (iemand sloeg op, node.changed) is een nieuw detail en een nieuwe weergave. Maar een tekst
// die je aan het bewerken bent, mag daardoor niet opnieuw beginnen: TextView krijgt fileKeyOf
// (alleen het bestand en de versie), en houdt het oude detail tot het nieuwe er is (keepLoaded);
// TextView zelf ziet de nieuwe etag en meldt "buiten de editor veranderd" (textEdit.ts).

import type { ViewerItem } from "./types";

/** De inhoud: bestand, etag en versie. */
export function keyOf(item: ViewerItem): string {
  return `${item.id}|${item.etag ?? ""}|${item.version?.id ?? ""}`;
}

/** Het bestand (en de versie), zonder de etag. */
export function fileKeyOf(item: ViewerItem): string {
  return `${item.id}|${item.version?.id ?? ""}`;
}

/** Wat de viewer heeft geladen: voor welke inhoud (key) en welk bestand (file). */
export interface LoadedLike<D> {
  key: string;
  file: string;
  detail?: D;
  error?: unknown;
}

/** Het detail om nu te tonen: dat van deze inhoud; anders, terwijl het nieuwe nog komt, het
 * vorige van hetzelfde bestand als keep(detail) het wil (een tekst). Anders null (laden). */
export function shownLoaded<D, L extends LoadedLike<D>>(
  loaded: L | null,
  key: string,
  file: string,
  keep: (detail: D) => boolean,
): L | null {
  if (!loaded) return null;
  if (loaded.key === key) return loaded;
  return loaded.file === file && loaded.detail !== undefined && keep(loaded.detail) ? loaded : null;
}
