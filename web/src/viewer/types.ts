// Het contract van de viewer (frozen na P0): wat Bestanden, Zoeken, Foto's, Gedeeld met mij en
// Versies aan de viewer geven. P4 bouwt de viewer zelf.

export interface ViewerItem {
  id: string;
  name: string;
  mime: string | null;
  size: number;
  modified_at: string;
  etag?: string;
  /** Een eerdere versie van het bestand bekijken (Versies…) */
  version?: { id: string; label: string };
}

/** Waar de viewer de inhoud vandaan haalt (ingelogd, een publieke link). Alleen P4 maakt ze. */
export type ViewerSource = { readonly kind: string };

export interface ViewerProps {
  items: readonly ViewerItem[];
  index: number;
  onIndexChange(i: number): void;
  onClose(): void;
  /** Aan het begin (-1) of eind (1) van items: de ouder kan er meer bij laden */
  onNeedMore?(direction: 1 | -1): void;
  source?: ViewerSource;
}
