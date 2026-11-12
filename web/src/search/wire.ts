// De querystring voor de API van fase 6 (§3.4): /search, /photos, de kaart en de personen nemen
// dezelfde filters. Een lijst wordt een sleutel per waarde (?type=image&type=video); tekst zonder
// spaties eromheen; wat leeg of niet gezet is, laat hij weg. Los van React en van de schil, zodat
// het te testen is.
//
// Niet te verwarren met de adresbalk van /zoeken (dat is van Z4).

export type WireValue = string | number | boolean | null | undefined | readonly (string | number)[];

function text(v: string | number | boolean): string {
  return typeof v === "string" ? v.trim() : String(v);
}

/** "?a=1&type=x&type=y" van de gezette velden, in de volgorde van het object, of "". */
export function wireQuery(params: object): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params) as [string, WireValue][]) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const v of value) if (text(v) !== "") q.append(key, text(v));
    } else if (text(value as string | number | boolean) !== "") {
      q.set(key, text(value as string | number | boolean));
    }
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Een id in een pad. */
export const seg = encodeURIComponent;
