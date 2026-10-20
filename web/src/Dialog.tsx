import { type ReactNode, useEffect, useId, useRef } from "react";
import "./files.css";

/** Een modaal venster (<dialog>): Escape en de sluitknop roepen onClose aan. */
export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => dialog?.close();
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
