// Het zoekveld in de balk bovenaan (fase 6, Z0 en Z4; Keuze 45 en 46), in de initiële JS: een
// combobox. Ctrl/Cmd+K zet de focus erop en Enter gaat naar /zoeken?q=…. Na de eerste toets laadt
// de lijst met suggesties (Suggest.tsx, een eigen chunk met het gebied search); die luistert zelf
// naar ↓, ↑, Enter op een optie en Esc zolang de lijst open is, en zet hier de aria-attributen.
//
// Escape (met de lijst dicht): een veld met tekst wordt leeg; een leeg veld geeft de focus terug
// aan waar hij stond vóór Ctrl/Cmd+K. Op Bestanden zet / de focus in dit veld (#zoekveld; Z4
// besliste, zie toetsenbord-z4.md).

import {
  type ComponentType,
  type FormEvent,
  type JSX,
  type KeyboardEvent,
  lazy,
  Suspense,
  useCallback,
  useRef,
  useState,
} from "react";
import { loadAreas, t } from "../i18n";
import { navigate } from "../router";
import { formatCombo, isMacPlatform, useShortcuts } from "../ui/keys";
import { ariaKeys, MAX_QUERY, SEARCH_INPUT_ID, SEARCH_KEY, SUGGEST_LIST_ID, searchShortcut, searchTarget } from "./appBar";
import type { ComboState } from "./combo";
import type { SuggestProps } from "./Suggest";

// Lukt het laden niet (geen verbinding, een oude versie na een update), dan blijft het een gewoon
// veld: Enter werkt
const Suggest = lazy<ComponentType<SuggestProps>>(() =>
  Promise.all([import("./Suggest"), loadAreas(["search"])]).then(
    ([m]) => m,
    () => ({ default: () => null }),
  ),
);

export function AppBarSearch(): JSX.Element {
  const field = useRef<HTMLInputElement>(null);
  // Waar de focus stond vóór Ctrl/Cmd+K
  const back = useRef<Element | null>(null);
  const [typed, setTyped] = useState(false);
  const [combo, setCombo] = useState<ComboState>({ open: false, active: null });
  const chosen = useCallback(() => {
    back.current = null;
  }, []);

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
    // De lijst met suggesties had hem al (Esc sloot de lijst)
    if (e.key !== "Escape" || e.defaultPrevented) return;
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
        id={SEARCH_INPUT_ID}
        type="search"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={combo.open}
        aria-controls={combo.open ? SUGGEST_LIST_ID : undefined}
        aria-activedescendant={combo.active ?? undefined}
        aria-label={t("shell.search.label")}
        aria-keyshortcuts={ariaKeys(isMacPlatform())}
        placeholder={t("shell.search.placeholder", { keys: formatCombo(SEARCH_KEY) })}
        maxLength={MAX_QUERY}
        enterKeyHint="search"
        autoComplete="off"
        onInput={() => setTyped(true)}
        onKeyDown={keyDown}
      />
      {typed && (
        <Suspense fallback={null}>
          <Suggest field={field} onState={setCombo} onChosen={chosen} />
        </Suspense>
      )}
    </form>
  );
}
