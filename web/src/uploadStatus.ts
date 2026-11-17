// Hoe de schil de uploads ziet, zonder de wachtrij en de tus-client in de eerste lading te
// trekken (frozen na P0). uploadQueue.ts meldt de wachtrij hier aan zodra die module laadt; de
// schil leest alleen "is er iets bezig" (dan pas het chunk van de uploadlade) en stopt alles bij
// uitloggen. Zolang de wachtrij niet geladen is, loopt er ook geen upload.

/** Wat de wachtrij hier aanmeldt (P3 houdt dat in uploadQueue.ts). */
export interface UploadSource {
  subscribe(listener: () => void): () => void;
  /** Staat er iets in de wachtrij (bezig, wachtend of mislukt)? */
  busy(): boolean;
  /** Bij uitloggen: alles stoppen en de lijst leegmaken. */
  stopAll(): void;
}

let source: UploadSource | null = null;
let detach: (() => void) | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  for (const l of listeners) l();
}

export const uploadStatus = {
  /** De wachtrij aanmelden (één tegelijk; een nieuwe vervangt de vorige). */
  attach(next: UploadSource): void {
    detach?.();
    source = next;
    detach = next.subscribe(notify);
    notify();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  busy(): boolean {
    return source?.busy() ?? false;
  },
  stopAll(): void {
    source?.stopAll();
  },
};
