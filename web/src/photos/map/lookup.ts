// Wat de kaart per omslag (een node-id) ophaalt en onthoudt zolang de pagina open is: de
// thumbnail (POST /previews, 500 per keer) en, vanaf zoom 6, de plaatsnaam (GET
// /files/{id}/location, één per keer; Keuze 32: "plaatsnamen op de kaart pas vanaf zoom 6").
// Alleen wat nog niet bekend is, en niet meer als het venster intussen veranderde (signal). Puur,
// zodat het in vitest te testen is.

import type { FileLocation, PlaceName } from "./types";

/** Vanaf deze zoom zet de kaart een plaatsnaam bij een stip. */
export const PLACE_ZOOM = 6;
/** Hooguit zoveel plaatsnamen tegelijk op de kaart (de grootste groepen). */
export const MAX_LABELS = 16;

export class Lookup<V> {
  private readonly got = new Map<string, V | null>();
  private readonly busy = new Set<string>();

  /**
   * fetch haalt een stuk ids op; wat er niet in het antwoord staat, is null (onbekend of niet
   * van jou: dan geen naam of geen thumbnail). Gooit fetch (geen verbinding, afgebroken), dan
   * onthoudt de kaart niets: de volgende keer vraagt hij het opnieuw. size: hoeveel ids per keer;
   * parallel: hoeveel keer tegelijk.
   */
  constructor(
    private readonly fetch: (ids: string[], signal?: AbortSignal) => Promise<Map<string, V | null>>,
    private readonly onChange: () => void,
    private readonly size = 1,
    private readonly parallel = 2,
  ) {}

  /** undefined: (nog) niet opgehaald; null: er is niets. */
  get(id: string): V | null | undefined {
    return this.got.get(id);
  }

  /** Haal op wat nog niet bekend is. Na afbreken (signal) begint er geen nieuw stuk meer. */
  async want(ids: readonly string[], signal?: AbortSignal): Promise<void> {
    const todo = [...new Set(ids)].filter((id) => !this.got.has(id) && !this.busy.has(id));
    const chunks: string[][] = [];
    for (let i = 0; i < todo.length; i += this.size) chunks.push(todo.slice(i, i + this.size));
    const worker = async () => {
      for (let chunk = chunks.shift(); chunk; chunk = chunks.shift()) {
        if (signal?.aborted) return;
        for (const id of chunk) this.busy.add(id);
        let answer: Map<string, V | null> | null = null;
        try {
          answer = await this.fetch(chunk, signal);
        } catch {
          answer = null;
        }
        for (const id of chunk) {
          this.busy.delete(id);
          if (answer) this.got.set(id, answer.get(id) ?? null);
        }
        if (answer) this.onChange();
      }
    };
    await Promise.all(Array.from({ length: Math.min(this.parallel, chunks.length) }, worker));
  }
}

/** De plaatsnaam bij een plek, of null zonder plaats. */
export function placeOf(loc: FileLocation | null): PlaceName | null {
  return loc?.place ?? null;
}

/** De omslagen waarvoor de kaart een naam zoekt: alleen vanaf PLACE_ZOOM, de grootste groepen
 * eerst, hooguit MAX_LABELS. */
export function labelCovers(dots: readonly { n: number; cell: { cover: string } }[], zoom: number): string[] {
  if (zoom < PLACE_ZOOM) return [];
  return [...dots]
    .sort((a, b) => b.n - a.n)
    .slice(0, MAX_LABELS)
    .map((d) => d.cell.cover);
}
