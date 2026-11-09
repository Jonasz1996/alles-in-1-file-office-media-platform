// Eén groep op /dubbel (fase 6, Z5; Keuze 36): de voorbeelden naast elkaar, met datum, grootte en
// map, een vinkje per bestand (nooit vooraf aangevinkt), "Naar de prullenbak" voor wat aangevinkt
// is en "Niet gelijk". Alleen weergave: de pagina (DupesPage.tsx) doet de verzoeken.

import { type JSX, type KeyboardEvent, useRef } from "react";
import type { Preview } from "../api";
import { previewCache } from "../browser/previews";
import { fmt, t } from "../i18n";
import { Link } from "../links";
import { Thumb } from "../Thumb";
import { Button } from "../ui/Button";
import { folderOf, levelOf, moveInGroup } from "./model";
import type { DupeGroup } from "./types";

export interface GroupProps {
  group: DupeGroup;
  /** De aangevinkte ids van deze groep; leeg tot de gebruiker zelf iets aanvinkt */
  picked: ReadonlySet<string>;
  previews: Readonly<Record<string, Preview>>;
  /** Er loopt een actie op deze groep */
  busy?: boolean;
  onPick(id: string, on: boolean): void;
  onTrash(): void;
  onNotSame(): void;
  onView(id: string): void;
}

/** Het id van de kop van een groep (voor de focus na een actie). */
export function groupHeadingId(key: string): string {
  return `dubbel-groep-${key.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

export function Group({ group, picked, previews, busy = false, onPick, onTrash, onNotSame, onView }: GroupProps): JSX.Element {
  const list = useRef<HTMLUListElement>(null);
  const level = levelOf(group);
  const headingId = groupHeadingId(group.key);
  const n = group.items.filter((it) => picked.has(it.node.id)).length;
  const hintId = `${headingId}-hint`;

  // Links en rechts gaan naar hetzelfde soort knop bij het vorige of volgende bestand
  function onKeyDown(e: KeyboardEvent<HTMLUListElement>) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const target = e.target as HTMLElement;
    const role = target.dataset.dupe;
    const li = target.closest<HTMLElement>("li[data-index]");
    if (!role || !li) return;
    const next = moveInGroup(e.key, Number(li.dataset.index), group.items.length);
    if (next === null) return;
    e.preventDefault();
    list.current?.querySelector<HTMLElement>(`li[data-index="${next}"] [data-dupe="${role}"]`)?.focus();
  }

  return (
    <section className="dupe-group" aria-labelledby={headingId}>
      <h2 id={headingId} tabIndex={-1} className="dupe-group-title">
        <span className={`dupe-level dupe-level-${level}`}>{t(`dupes.level.${level}`)}</span>
        <span className="muted">{t("dupes.group.files", { n: group.items.length })}</span>
      </h2>
      <ul ref={list} className="dupe-items" onKeyDown={onKeyDown}>
        {group.items.map((it, i) => {
          const node = it.node;
          const on = picked.has(node.id);
          const folder = folderOf(it, t("dupes.myFiles"));
          return (
            <li key={node.id} data-index={i} className={on ? "dupe-item picked" : "dupe-item"}>
              <button
                type="button"
                className="dupe-view"
                data-dupe="view"
                aria-label={t("dupes.item.view", { name: node.name })}
                onClick={() => onView(node.id)}
              >
                <Thumb
                  node={node}
                  preview={previews[node.id]}
                  pending={previewCache.pending(node.id, node.etag)}
                  size={180}
                />
              </button>
              <p className="dupe-name" title={node.name}>
                {node.name}
              </p>
              <dl className="dupe-facts">
                <dt>{it.taken_at ? t("dupes.item.taken") : t("dupes.item.modified")}</dt>
                <dd>{it.taken_at ? fmt.wallClock(it.taken_at) : fmt.dateTime(node.modified_at)}</dd>
                <dt>{t("dupes.item.size")}</dt>
                <dd>{fmt.bytes(it.size)}</dd>
                <dt>{t("dupes.item.folder")}</dt>
                <dd>
                  <Link to={folder.href} title={folder.label} data-dupe="folder">
                    {folder.label}
                  </Link>
                </dd>
              </dl>
              <label className="dupe-pick">
                <input
                  type="checkbox"
                  data-dupe="pick"
                  checked={on}
                  disabled={busy}
                  aria-label={t("dupes.item.pickLabel", { name: node.name })}
                  onChange={(e) => onPick(node.id, e.currentTarget.checked)}
                />
                {t("dupes.item.pick")}
              </label>
            </li>
          );
        })}
      </ul>
      <div className="dupe-actions">
        <Button
          variant="danger"
          icon="trash"
          disabled={n === 0}
          busy={busy}
          aria-describedby={n === 0 ? hintId : undefined}
          onClick={onTrash}
        >
          {n === 0 ? t("dupes.trash.none") : t("dupes.trash.some", { n })}
        </Button>
        <Button disabled={busy} onClick={onNotSame}>
          {t("dupes.notSame")}
        </Button>
        {n === 0 && (
          <span id={hintId} className="muted">
            {t("dupes.trash.hint")}
          </span>
        )}
      </div>
    </section>
  );
}
