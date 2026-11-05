// Meer personen kiezen (de filters op /fotos en de regel van een album): de combobox met je
// personen, en de gekozen personen als knopjes om weer weg te halen (daarna staat de focus weer
// in het veld). Laadt je personen zelf (ook de verborgen, voor de namen van wat al gekozen is).

import { type JSX, useEffect, useMemo, useRef, useState } from "react";
import { t } from "../../i18n";
import { IconButton } from "../../ui/Button";
import { people } from "./api";
import { PersonCombo } from "./PersonCombo";
import type { Person } from "./types";

export function PersonPicker({
  label,
  hint,
  selected,
  onChange,
}: {
  label: string;
  hint?: string;
  selected: readonly string[];
  onChange(ids: readonly string[]): void;
}): JSX.Element {
  const [shown, setShown] = useState<readonly Person[]>([]);
  const [hidden, setHidden] = useState<readonly Person[]>([]);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    Promise.all([people(false), people(true)]).then(
      ([a, b]) => {
        if (!live) return;
        setShown(a.people);
        setHidden(b.people);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, []);

  const names = useMemo(() => new Map([...shown, ...hidden].map((p) => [p.id, p.name])), [shown, hidden]);
  const exclude = useMemo(() => new Set(selected), [selected]);

  return (
    <div ref={wrap} className="person-picker">
      <PersonCombo
        label={label}
        hint={hint}
        people={shown}
        exclude={exclude}
        onPick={(o) => {
          if (o.kind === "person" && !selected.includes(o.person.id)) onChange([...selected, o.person.id]);
        }}
      />
      {selected.length > 0 && (
        <ul className="filter-chips">
          {selected.map((id) => {
            const name = names.get(id) ?? t("photos_people.person");
            return (
              <li key={id}>
                {name}
                <IconButton
                  icon="close"
                  className="ghost"
                  label={t("photos_people.filters.remove", { name })}
                  onClick={() => {
                    onChange(selected.filter((x) => x !== id));
                    requestAnimationFrame(() => wrap.current?.querySelector<HTMLInputElement>("[role=combobox]")?.focus());
                  }}
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
