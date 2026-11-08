// De suggesties onder het zoekveld bovenaan (fase 6, Z4; Keuze 46 en 81): een eigen lazy chunk,
// pas geladen na de eerste toets in het veld (AppBarSearch.tsx). Een ARIA-combobox: de focus
// blijft in het veld, de lijst is een listbox met groepen (namen, personen, plaatsen, tags,
// albums, elk ≤ 5) en als laatste "Zoek '…' op wat er op je foto's staat" (geen gast).
//
// 120 ms na de laatste toets vraagt hij GET /search/suggest; een antwoord op een oudere vraag, of
// als het veld intussen anders is of de focus weg is, telt niet. De toetsen (onKey in combo.ts)
// komen binnen via een luisteraar op het veld zelf, vóór die van React in AppBarSearch: ↓ en ↑
// door de opties (aria-activedescendant), Enter kiest de actieve optie (zonder actieve optie gaat
// Enter naar /zoeken, zoals altijd), Esc sluit de lijst (de tekst blijft; een tweede Esc is weer
// van het veld), Tab sluit hem en gaat verder. Kiezen maakt het veld leeg, zoals Enter.

import { type JSX, type RefObject, useEffect, useRef, useState } from "react";
import { api, type FileNode } from "../api";
import { isGuest, useCurrentUser } from "../currentUser";
import { type MessageKey, t } from "../i18n";
import { nodeHref } from "../links";
import { navigate } from "../router";
import { suggest } from "./api";
import { SUGGEST_LIST_ID } from "./appBar";
import {
  type ComboState,
  fallbackHref,
  fieldState,
  grouped,
  type ListState,
  type Option,
  onKey,
  optionsOf,
  type SuggestGroup,
} from "./combo";
import { seg } from "./wire";
import "./suggest.css";

/** Zo lang na de laatste toets (Keuze 46). */
export const WAIT_MS = 120;

const GROUP_LABEL: Record<Exclude<SuggestGroup, "image">, MessageKey> = {
  names: "search.suggest.names",
  people: "search.suggest.people",
  places: "search.suggest.places",
  tags: "search.suggest.tags",
  albums: "search.suggest.albums",
};

/** De lijst zelf (role=listbox), zonder toestand: wat Suggest toont als hij open is. */
export function SuggestList({
  options,
  active,
  onActive,
  onChoose,
}: {
  options: readonly Option[];
  active: number;
  onActive: (i: number) => void;
  onChoose: (o: Option) => void;
}): JSX.Element {
  return (
    <div id={SUGGEST_LIST_ID} role="listbox" aria-label={t("search.suggest.label")} className="suggest">
      {grouped(options).map(([group, items]) => {
        const rows = items.map(([i, o]) => (
          <div
            key={o.id}
            id={o.id}
            role="option"
            aria-selected={i === active}
            className={group === "image" ? "suggest-option suggest-image" : "suggest-option"}
            // De focus blijft in het veld
            onMouseDown={(e) => e.preventDefault()}
            onMouseMove={() => i !== active && onActive(i)}
            onClick={() => onChoose(o)}
          >
            {group === "image" ? t("search.suggest.image", { q: o.label }) : o.label}
          </div>
        ));
        if (group === "image") return rows;
        return (
          <div key={group} role="group" aria-label={t(GROUP_LABEL[group])}>
            <div className="suggest-head" aria-hidden="true">
              {t(GROUP_LABEL[group])}
            </div>
            {rows}
          </div>
        );
      })}
    </div>
  );
}

export interface SuggestProps {
  field: RefObject<HTMLInputElement | null>;
  /** Open of dicht en de actieve optie, voor de aria-attributen van het veld */
  onState: (state: ComboState) => void;
  /** Na een keuze (het veld is dan leeg) */
  onChosen: () => void;
}

/** Waar een keuze heen gaat; een bestand opent in zijn map (de viewer met ?bestand=). */
async function go(o: Option): Promise<void> {
  const target = o.target;
  if ("href" in target) {
    navigate(target.href);
    return;
  }
  try {
    const node = await api.request<FileNode>("GET", `/api/v1/files/${seg(target.file)}`);
    navigate(nodeHref(node));
  } catch {
    navigate(fallbackHref(target.name));
  }
}

const SHUT: ListState = { open: false, active: -1 };

export default function Suggest({ field, onState, onChosen }: SuggestProps): JSX.Element | null {
  const withImage = !isGuest(useCurrentUser());
  const [options, setOptions] = useState<Option[]>([]);
  const [list, setList] = useState<ListState>(SHUT);
  // Voor de luisteraars op het veld, die blijven staan
  const now = useRef({ options, list });
  now.current = { options, list };
  const choose = useRef<(o: Option) => void>(() => {});
  const chosen = useRef(onChosen);
  chosen.current = onChosen;

  const state = fieldState(list, options);
  useEffect(() => {
    onState({ open: state.open, active: state.active });
  }, [state.open, state.active, onState]);

  // De actieve optie in beeld (de lijst scrolt, de focus blijft in het veld)
  useEffect(() => {
    if (state.active) document.getElementById(state.active)?.scrollIntoView?.({ block: "nearest" });
  }, [state.active]);

  useEffect(() => {
    const el = field.current;
    if (!el) return;
    let timer = 0;
    let seq = 0;

    function close(): void {
      window.clearTimeout(timer);
      seq++;
      setList(SHUT);
    }

    function ask(): void {
      window.clearTimeout(timer);
      const mine = ++seq;
      const q = el?.value.trim() ?? "";
      if (!q) {
        setOptions([]);
        setList(SHUT);
        return;
      }
      timer = window.setTimeout(() => {
        suggest(q)
          .then(
            (s) => s,
            () => null, // 429 of geen verbinding: alleen de laatste regel
          )
          .then((s) => {
            if (mine !== seq || !el || el.value.trim() !== q || document.activeElement !== el) return;
            setOptions(optionsOf(s, q, withImage));
            setList({ open: true, active: -1 });
          });
      }, WAIT_MS);
    }

    choose.current = (o: Option) => {
      close();
      if (el) el.value = "";
      setOptions([]);
      chosen.current();
      void go(o);
    };

    function key(e: KeyboardEvent): void {
      const { options, list } = now.current;
      const r = onKey(list, options.length, e);
      if (!r) return;
      if (r.do === "show") {
        e.preventDefault(); // de cursor in het veld blijft staan
        setList(r.state);
      } else if (r.do === "choose") {
        e.preventDefault(); // geen /zoeken: de optie
        choose.current(options[r.index]);
      } else {
        if (r.prevent) e.preventDefault(); // Esc: niet ook nog het veld leeg
        close();
      }
    }

    // De eerste toets kwam vóór dit chunk er was
    ask();
    const form = el.form;
    el.addEventListener("input", ask);
    el.addEventListener("keydown", key);
    el.addEventListener("blur", close);
    form?.addEventListener("submit", close);
    return () => {
      window.clearTimeout(timer);
      seq++;
      el.removeEventListener("input", ask);
      el.removeEventListener("keydown", key);
      el.removeEventListener("blur", close);
      form?.removeEventListener("submit", close);
    };
  }, [field, withImage]);

  if (!state.open) return null;
  return (
    <SuggestList
      options={options}
      active={list.active}
      onActive={(i) => setList({ open: true, active: i })}
      onChoose={(o) => choose.current(o)}
    />
  );
}
