// "Al je foto's in beeld" (Home, Keuze 31): het stuk wereld met al je foto's, met de filters van
// nu. De server geeft alleen cellen, dus de kaart vraagt eerst zoom 0 (zestien cellen over de
// hele wereld), past het venster op wat er staat, en vraagt dan dieper, tot het venster niet meer
// verandert. Bij één stad zijn dat een paar kleine verzoeken. Puur (het ophalen komt binnen als
// functie), zodat het in vitest te testen is met vaste antwoorden.

import type { Filters } from "../../search/types";
import { cellsBounds, requestBBox } from "./cells";
import { cellsPath } from "./requests";
import { MAX_CELLS, type MapCells } from "./types";
import { cellZoom, fitRect, type Rect, type Size, WORLD } from "./view";

/** Hooguit zoveel verzoeken voor één keer Home (zoom 0 en dan vier tot vijf stappen dieper). */
const MAX_STEPS = 6;

export type GetCells = (path: string, signal?: AbortSignal) => Promise<MapCells>;

/** Het stuk wereld met al je foto's, of null zonder foto's (met een plek en deze filters). */
export async function homeRect(get: GetCells, filters: Filters, size: Size, signal?: AbortSignal): Promise<Rect | null> {
  let z = 0;
  let rect: Rect = WORLD;
  let best: Rect | null = null;
  for (let step = 0; step < MAX_STEPS; step++) {
    const res = await get(cellsPath(requestBBox(rect), z, filters), signal);
    // Te veel cellen: de server gaf er niet alles, dan blijft het vorige (grovere) stuk
    if (best && res.cells.length >= MAX_CELLS) return best;
    const bounds = cellsBounds(res.cells, z);
    if (!bounds) return best;
    best = bounds;
    const next = cellZoom(fitRect(bounds, size));
    if (next <= z) return best;
    z = next;
    rect = bounds;
  }
  return best;
}
