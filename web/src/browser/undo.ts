// "Ongedaan maken" na naar de prullenbak (P2): de melding met de knop, en Mod+Z zolang precies
// die melding er staat. Blijft hij langer staan (de muis of de focus erop), dan werkt Mod+Z ook
// langer; is hij weg (verlopen, gesloten met ×, verdrongen door nieuwere meldingen of na
// uitloggen), dan werkt Mod+Z ook niet meer.

import { toast, toasts } from "../ui/toast";

interface PendingUndo {
  /** Het id van de melding */
  id: string;
  run(): void;
}

let pending: PendingUndo | null = null;
let seq = 0;

/** Toont de melding met de knop label; run draait bij die knop of bij Mod+Z (één keer). */
export function offerUndo(message: string, label: string, run: () => void): void {
  const undo: PendingUndo = { id: `undo-${++seq}`, run };
  toast(message, {
    id: undo.id,
    action: {
      label,
      run: () => {
        if (pending === undo) pending = null;
        run();
      },
    },
  });
  pending = undo;
}

function shown(u: PendingUndo | null): u is PendingUndo {
  return u !== null && toasts.getSnapshot().some((x) => x.id === u.id);
}

/** Is er iets om ongedaan te maken (de melding staat nog)? */
export function canUndo(): boolean {
  return shown(pending);
}

/** Mod+Z: hetzelfde als "Ongedaan maken" in de melding (die gaat dan weg). */
export function undoLast(): void {
  const u = pending;
  pending = null;
  if (!shown(u)) return;
  toasts.dismiss(u.id);
  u.run();
}

/** Bij uitloggen: niets meer ongedaan te maken, en de melding weg. */
export function clearUndo(): void {
  const u = pending;
  pending = null;
  if (u) toasts.dismiss(u.id);
}
