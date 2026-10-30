import { type JSX, type ReactNode, useId, useLayoutEffect, useRef } from "react";
import { t } from "../i18n";
import { IconButton } from "./Button";
import { pushScope } from "./keys";
import { modals } from "./topLayer";

export interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  size?: "s" | "m" | "l" | "full";
  /** Knoppen onderaan (rechts uitgelijnd) */
  actions?: ReactNode;
}

/**
 * Een modaal venster (<dialog>, in de top layer). Escape en de sluitknop roepen onClose aan; de
 * ouder haalt de dialoog dan weg. Een element met data-autofocus krijgt de focus bij het openen.
 * Na het sluiten staat de focus weer waar hij was. Zolang hij open is, staat de sneltoets-scope
 * "dialog" bovenop: sneltoetsen van de pagina eronder werken dan niet. Een scope van iets ín de
 * dialoog (useScope("viewer")) komt erboven: de dialoog zet de zijne in een layout-effect, en die
 * lopen allemaal vóór de gewone effecten van de kinderen. Meldingen verschijnen in de bovenste
 * open dialoog (zie topLayer.ts en ToastRegion).
 */
export function Dialog({ title, onClose, children, size = "m", actions }: DialogProps): JSX.Element {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useLayoutEffect(() => {
    const dialog = ref.current;
    const before = document.activeElement;
    const popScope = pushScope("dialog");
    if (dialog && !dialog.open) dialog.showModal();
    const leave = dialog ? modals.open(dialog) : () => {};
    dialog?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    return () => {
      popScope();
      leave();
      dialog?.close();
      // React haalt de dialoog al weg voordat dit draait; dan zet de browser de focus niet
      // zelf terug
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className={`dialog size-${size}`}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault(); // de ouder beslist; die haalt de dialoog weg
        onClose();
      }}
    >
      <header>
        <h2 id={titleId}>{title}</h2>
        <IconButton icon="close" className="ghost" label={t("common.close")} onClick={onClose} />
      </header>
      {children}
      {actions && <div className="dialog-actions">{actions}</div>}
    </dialog>
  );
}
