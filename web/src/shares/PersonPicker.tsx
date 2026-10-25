// Een persoon of groep kiezen om mee te delen: een combobox (WAI-ARIA 1.2) met een lijst die
// onder het veld openklapt. ↑ en ↓ gaan door de lijst (aria-activedescendant: de focus blijft in
// het veld), Enter kiest, Escape sluit de lijst of maakt het veld leeg.

import { type JSX, type KeyboardEvent, type RefObject, useEffect, useId, useRef, useState } from "react";
import { api, type Directory } from "../api";
import { errorText, t } from "../i18n";
import { Icon } from "../ui/Icon";
import { memberCount } from "../sharing";

export type ShareTarget = { user_id: string } | { group_id: string };

export interface Option {
  key: string;
  target: ShareTarget;
  name: string;
  detail: string;
  group: boolean;
}

/** De keuzes uit een zoekresultaat, zonder wie er al bij is. */
export function optionsOf(
  found: Directory | null,
  exclude: { users: ReadonlySet<string>; groups: ReadonlySet<string> },
): Option[] {
  if (!found) return [];
  return [
    ...found.users
      .filter((u) => !exclude.users.has(u.id))
      .map((u) => ({ key: `u-${u.id}`, target: { user_id: u.id }, name: u.display_name, detail: u.email, group: false })),
    ...found.groups
      .filter((g) => !exclude.groups.has(g.id))
      .map((g) => ({
        key: `g-${g.id}`,
        target: { group_id: g.id },
        name: g.name,
        detail: t("sharing.people.group", { members: memberCount(g.member_count) }),
        group: true,
      })),
  ];
}

/** De volgende actieve keuze bij ↑ of ↓ (rond: na de laatste weer de eerste). -1 = geen. */
export function nextActive(current: number, count: number, step: 1 | -1): number {
  if (count <= 0) return -1;
  if (current < 0) return step === 1 ? 0 : count - 1;
  return (current + step + count) % count;
}

export function PersonPicker({
  exclude,
  disabled,
  onPick,
  inputRef,
  onError,
}: {
  exclude: { users: ReadonlySet<string>; groups: ReadonlySet<string> };
  disabled?: boolean;
  onPick(target: ShareTarget, name: string): Promise<boolean> | boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
  onError(message: string): void;
}): JSX.Element {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Directory | null>(null);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const searches = useRef(0);
  const id = useId();
  const listId = `${id}-list`;
  const own = useRef<HTMLInputElement>(null);
  const input = inputRef ?? own;

  useEffect(() => {
    const text = q.trim().slice(0, 100);
    const seq = ++searches.current;
    if (!text) {
      setFound(null);
      return;
    }
    // Pas zoeken als er even niet getypt wordt
    const timer = setTimeout(() => {
      api
        .directory(text)
        .then((d) => {
          if (seq !== searches.current) return;
          setFound(d);
          setActive(-1);
          setOpen(true);
        })
        .catch((e: unknown) => {
          if (seq === searches.current) onError(errorText(e));
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [q, onError]);

  const options = optionsOf(found, exclude);
  const nothing = found !== null && options.length === 0;
  const expanded = open && options.length > 0;

  async function pick(o: Option) {
    if (disabled) return;
    if (await onPick(o.target, o.name)) {
      setQ("");
      setFound(null);
      setActive(-1);
      setOpen(false);
      input.current?.focus();
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (options.length === 0) return;
      e.preventDefault();
      setOpen(true);
      setActive((a) => nextActive(a, options.length, e.key === "ArrowDown" ? 1 : -1));
    } else if (e.key === "Enter") {
      const o = expanded ? options[active >= 0 ? active : 0] : undefined;
      if (o) {
        e.preventDefault();
        void pick(o);
      }
    } else if (e.key === "Escape") {
      if (expanded) {
        e.preventDefault();
        e.stopPropagation(); // niet ook de dialoog sluiten
        setOpen(false);
        setActive(-1);
      } else if (q) {
        e.preventDefault();
        e.stopPropagation();
        setQ("");
      }
    }
  }

  return (
    <div className="share-picker">
      <input
        ref={input}
        type="text"
        role="combobox"
        aria-label={t("sharing.people.search")}
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={expanded && active >= 0 ? `${id}-${active}` : undefined}
        aria-keyshortcuts="Alt+P"
        autoComplete="off"
        placeholder={t("sharing.people.placeholder")}
        maxLength={100}
        value={q}
        disabled={disabled}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onFocus={() => setOpen(true)}
      />
      <ul id={listId} role="listbox" className="share-suggestions" aria-label={t("sharing.people.suggestions")} hidden={!expanded}>
        {options.map((o, i) => (
          <li
            key={o.key}
            id={`${id}-${i}`}
            role="option"
            aria-selected={i === active}
            className={i === active ? "share-option active" : "share-option"}
            onMouseDown={(e) => e.preventDefault()} // de focus blijft in het veld
            onClick={() => void pick(o)}
          >
            <Icon name="user" size={16} />
            <span>
              {o.name} <span className="muted">{o.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      {nothing && (
        <p className="muted" role="status">
          {t("sharing.people.noneFound")}
        </p>
      )}
    </div>
  );
}
