import { type ReactNode, useEffect, useId, useRef } from "react";
import "./files.css";

/** Een modaal venster (<dialog>): Escape en de sluitknop roepen onClose aan. Na het sluiten
 * staat de focus weer waar hij was (de knop die het opende), ook voor wie met het toetsenbord
 * werkt. */
export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    const before = document.activeElement;
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      dialog?.close();
      // React haalt de dialoog al weg voordat dit draait; dan zet de browser de focus niet
      // zelf terug
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault(); // de ouder beslist; die haalt de dialoog weg
        onClose();
      }}
    >
      <header>
        <h2 id={titleId}>{title}</h2>
        <button type="button" onClick={onClose} aria-label="Sluiten">
          ✕
        </button>
      </header>
      {children}
    </dialog>
  );
}
