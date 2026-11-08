// Een persoon kiezen uit je eigen personen (Keuze 37 en 38), met het ARIA-patroon van een
// combobox: het veld heeft role="combobox", aria-expanded, aria-controls en aria-activedescendant;
// ↓ en ↑ lopen door de lijst, Enter kiest, Esc sluit de lijst (en anders: onEscape). Gebruikt in het
// infopaneel (Persoon toevoegen, met een nieuwe naam erbij) en in de filters op /fotos.

import { type JSX, type KeyboardEvent, useId, useMemo, useState } from "react";
import { t } from "../../i18n";
import { type ComboOption, suggest } from "./combo";
import type { Person } from "./types";

export interface PersonComboProps {
  label: string;
  hint?: string;
  people: readonly Person[];
  /** Ids die niet in de lijst komen (al gekozen, al op de foto) */
  exclude?: ReadonlySet<string>;
  /** Een laatste optie "Nieuwe persoon: …" als niemand zo heet */
  allowNew?: boolean;
  /** Na kiezen het veld leeg (filters) of met de gekozen naam (infopaneel) */
  clearOnPick?: boolean;
  onPick(o: ComboOption): void;
  /** Er wordt weer getypt na een keuze: de keuze geldt niet meer */
  onText?(text: string): void;
  /** Esc terwijl de lijst dicht is */
  onEscape?(): void;
  autoFocus?: boolean;
}

export function PersonCombo(p: PersonComboProps): JSX.Element {
  const id = useId();
  const listId = `${id}-lijst`;
  const hintId = `${id}-hint`;
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const { people, exclude, allowNew } = p;
  const options = useMemo(() => suggest(people, text, { exclude, allowNew }), [people, text, exclude, allowNew]);
  const shown = open && options.length > 0;
  const at = Math.min(active, Math.max(0, options.length - 1));
  const optionId = (i: number) => `${id}-optie-${i}`;

  function pick(i: number) {
    const o = options[i];
    if (!o) return;
    p.onPick(o);
    setText(p.clearOnPick === false ? (o.kind === "person" ? o.person.name : o.name) : "");
    setOpen(false);
    setActive(0);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!shown) {
        setOpen(true);
        setActive(e.key === "ArrowDown" ? 0 : options.length - 1);
      } else {
        const step = e.key === "ArrowDown" ? 1 : -1;
        setActive((at + step + options.length) % options.length);
      }
    } else if (e.key === "Enter") {
      if (shown) {
        e.preventDefault();
        pick(at);
      }
    } else if (e.key === "Escape") {
      if (shown) {
        e.preventDefault();
        setOpen(false);
      } else if (p.onEscape) {
        e.preventDefault();
        p.onEscape();
      }
    }
  }

  return (
    <div className="field person-combo">
      <label htmlFor={id}>{p.label}</label>
      <input
        id={id}
        role="combobox"
        aria-expanded={shown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={shown ? optionId(at) : undefined}
        aria-describedby={p.hint ? hintId : undefined}
        autoComplete="off"
        spellCheck={false}
        autoFocus={p.autoFocus}
        // In een dialoog zet Dialog de focus hier (na showModal)
        data-autofocus={p.autoFocus ? "" : undefined}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
          p.onText?.(e.target.value);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => setOpen(false)}
      />
      {p.hint && (
        <small id={hintId} className="muted">
          {p.hint}
        </small>
      )}
      <ul id={listId} role="listbox" aria-label={t("photos_people.combo.list")} className="combo-list" hidden={!shown}>
        {options.map((o, i) => (
          <li
            key={o.kind === "person" ? o.person.id : "nieuw"}
            id={optionId(i)}
            role="option"
            aria-selected={i === at}
            className={i === at ? "active" : undefined}
            onMouseDown={(e) => {
              e.preventDefault(); // de focus blijft in het veld
              pick(i);
            }}
          >
            {o.kind === "person" ? o.person.name : t("photos_people.combo.create", { name: o.name })}
          </li>
        ))}
      </ul>
      {open && text.trim() !== "" && options.length === 0 && (
        <p className="muted" role="status">
          {t("photos_people.combo.none")}
        </p>
      )}
    </div>
  );
}
