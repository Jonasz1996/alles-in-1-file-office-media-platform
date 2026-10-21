// De kop van een pagina. Na een navigatie krijgt hij de focus (zodat schermlezers en het
// toetsenbord bij de nieuwe pagina beginnen), en hij zet document.title.

import { type JSX, type ReactNode, useEffect, useRef } from "react";
import { t } from "../i18n";

let focusWanted = false;
let wantedUntil = 0;

/** De schil vraagt dit na elke navigatie: de eerstvolgende PageTitle neemt de focus. */
export function requestPageFocus(): void {
  focusWanted = true;
  wantedUntil = Date.now() + 3000; // komt de kop pas veel later (trage lijst), dan niet meer
}

/** Neemt het verzoek; true als deze kop de focus moet nemen. */
function takePageFocus(): boolean {
  const wanted = focusWanted && Date.now() < wantedUntil;
  focusWanted = false;
  return wanted;
}

/** Geeft de focus aan de kop als die er al staat; anders aan main. Voor de schil. */
export function focusPage(): void {
  const h1 = document.querySelector<HTMLElement>("h1[data-page-title]");
  if (h1) {
    focusWanted = false;
    h1.focus({ preventScroll: true });
    return;
  }
  // Nog geen kop (de pagina laadt nog): main krijgt de focus, de kop neemt hem later over
  // zolang de gebruiker intussen niets anders koos
  document.getElementById("inhoud")?.focus({ preventScroll: true });
}

export function PageTitle({ children, actions }: { children: string; actions?: ReactNode }): JSX.Element {
  const ref = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    document.title = t("shell.pageTitle", { title: children });
  }, [children]);

  useEffect(() => {
    if (!takePageFocus()) return;
    const active = document.activeElement;
    // Alleen als de focus nog nergens bijzonder staat (body of main)
    if (active && active !== document.body && active.id !== "inhoud") return;
    ref.current?.focus({ preventScroll: true });
  }, []);

  return (
    <div className="page-title">
      <h1 ref={ref} tabIndex={-1} data-page-title="">
        {children}
      </h1>
      {actions && <div className="page-title-actions">{actions}</div>}
    </div>
  );
}
