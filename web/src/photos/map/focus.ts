// "Op de kaart" in de rij Plek (LocationRow): welke plek de kaart als eerste toont. Alleen in het
// geheugen van de pagina, nooit in het adres: een plek hoort niet in de adresbalk, in de
// geschiedenis van de browser of in het access-log (Keuze 27). Herladen toont gewoon al je foto's.

export interface MapFocus {
  lat: number;
  lon: number;
}

let pending: MapFocus | null = null;
let listener: ((f: MapFocus) => void) | null = null;

/** Deze plek in het midden van de kaart: meteen als de kaart al open is (de viewer op de kaart),
 * anders de volgende keer dat hij opent. */
export function showOnMap(lat: number, lon: number): void {
  const f = Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : null;
  if (f && listener) {
    pending = null;
    listener(f);
  } else {
    pending = f;
  }
}

/** De open kaart luistert; terug krijg je het afmelden. */
export function onMapFocus(l: (f: MapFocus) => void): () => void {
  listener = l;
  return () => {
    if (listener === l) listener = null;
  };
}

/** De plek die op de kaart moet (zonder hem weg te halen: een initializer van React mag twee keer
 * draaien). */
export function peekMapFocus(): MapFocus | null {
  return pending;
}

/** De kaart toont hem: de volgende keer opent de kaart weer gewoon. */
export function clearMapFocus(): void {
  pending = null;
}
