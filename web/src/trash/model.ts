// Losse functies voor de prullenbak, zonder React (te testen): waar iets stond, waar de acties
// op werken en wat er van een bulkactie terechtkwam.

import type { ItemResult } from "../crossApi";
import { t } from "../i18n";
import type { TrashItem } from "./api";

/** "Was in": het pad van de map waar het stond, of de hoofdmap. null voor een album. */
export function locationText(item: Pick<TrashItem, "location">): string | null {
  if (item.location === null) return null;
  if (item.location.length === 0) return t("trash.rootFolder");
  return item.location.map((p) => p.name).join(" / ");
}

/**
 * Waar een actie op werkt: de selectie in de volgorde van de lijst, of anders het item met de
 * focus. Leeg als er niets is.
 */
export function actionIds(order: readonly string[], selected: ReadonlySet<string>, focus: string | null): string[] {
  if (selected.size > 0) return order.filter((id) => selected.has(id));
  return focus !== null && order.includes(focus) ? [focus] : [];
}

export interface Outcome {
  ok: number;
  failed: ItemResult[];
}

/** Telt de resultaten van een bulkactie. */
export function outcome(results: readonly ItemResult[]): Outcome {
  const failed = results.filter((r) => !r.ok);
  return { ok: results.length - failed.length, failed };
}

/** Nieuwste eerst; bij gelijke tijd op naam. */
export function sortItems(items: readonly TrashItem[]): TrashItem[] {
  return [...items].sort((a, b) => {
    const x = a.node.deleted_at ?? "";
    const y = b.node.deleted_at ?? "";
    if (x !== y) return x < y ? 1 : -1;
    return a.node.name.localeCompare(b.node.name);
  });
}
