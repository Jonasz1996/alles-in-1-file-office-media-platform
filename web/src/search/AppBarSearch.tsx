// Het zoekveld in de balk bovenaan (fase 6, Z0; Keuze 45), in de initiële JS: een gewoon veld,
// Ctrl/Cmd+K zet de focus erop en Enter gaat naar /zoeken?q=… (de pagina van 0.7.0, tot Z4 haar
// vervangt). De suggesties (een lazy combobox na de eerste toets) zijn van Z4.
//
// Escape: een veld met tekst wordt leeg; een leeg veld geeft de focus terug aan waar hij stond
// vóór Ctrl/Cmd+K. Op Bestanden blijft / het zoekveld daar (#zoekveld), zie toetsenbord-z0.md.

import { type FormEvent, type JSX, type KeyboardEvent, useRef } from "react";
import { t } from "../i18n";
import { navigate } from "../router";
import { formatCombo, isMacPlatform, useShortcuts } from "../ui/keys";
import { ariaKeys, MAX_QUERY, SEARCH_KEY, searchShortcut, searchTarget } from "./appBar";

export function AppBarSearch(): JSX.Element {
  const field = useRef<HTMLInputElement>(null);
  // Waar de focus stond vóór Ctrl/Cmd+K
  const back = useRef<Element | null>(null);

  useShortcuts([
    searchShortcut(() => {
      const el = field.current;
      if (!el) return;
      if (document.activeElement !== el) back.current = document.activeElement;
      el.focus();
      el.select();
    }, t("shell.search.key")),
  ]);

  function submit(e: FormEvent): void {
    e.preventDefault();
    const el = field.current;
    const to = el && searchTarget(el.value);
    if (!to) return;
    el.value = "";
    back.current = null;
    navigate(to);
  }

  function keyDown(e: KeyboardEvent<HTMLInputElement>): void {
    if (e.key !== "Escape") return;
    const el = e.currentTarget;
    const prev = back.current;
    if (el.value) el.value = "";
    else if (prev instanceof HTMLElement && prev.isConnected) {
      back.current = null;
      prev.focus();
    } else return; // niets te doen: de pagina mag Escape hebben
    e.preventDefault();
  }

  return (
    <form role="search" className="appbar-search" aria-label={t("shell.search.region")} onSubmit={submit}>
      <input
        ref={field}
        type="search"
        aria-label={t("shell.search.label")}
        aria-keyshortcuts={ariaKeys(isMacPlatform())}
        placeholder={t("shell.search.placeholder", { keys: formatCombo(SEARCH_KEY) })}
        maxLength={MAX_QUERY}
        enterKeyHint="search"
        onKeyDown={keyDown}
      />
    </form>
  );
}
