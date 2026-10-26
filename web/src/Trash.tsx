// /prullenbak: wat je weggooide, met waar het stond ("Was in"), de grootte (een map: alles
// eronder) en het totaal. Selecteren zoals in de bestandslijst (klik, Ctrl/Cmd, Shift, de
// pijltjes, Spatie); Terugzetten (Enter), Definitief verwijderen (Shift+Delete of
// Shift+Mod+Backspace) en Prullenbak legen (Shift+L), steeds na een vraag als het niet terug kan.

import {
  type CSSProperties,
  type JSX,
  type KeyboardEvent,
  type MouseEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ItemResult } from "./crossApi";
import { useReloadOn } from "./events";
import { errorText, fmt, t } from "./i18n";
import type { RouteProps } from "./router";
import { trashApi, type TrashItem, type TrashOverview } from "./trash/api";
import { actionIds, locationText, outcome, sortItems } from "./trash/model";
import { Button } from "./ui/Button";
import { confirm } from "./ui/dialogs";
import { EmptyState } from "./ui/EmptyState";
import { Icon } from "./ui/Icon";
import { keyToMove, moveIndex, useScope, useShortcuts } from "./ui/keys";
import { PageTitle } from "./ui/PageTitle";
import { clickSelect, keySelect, SelectionStore, useSelection } from "./ui/selection";
import { toast } from "./ui/toast";
import { VirtualList } from "./ui/virtual";
import "./trash/trash.css";

const ROW_H = 52;

export default function Trash(_: RouteProps): JSX.Element {
  const [data, setData] = useState<TrashOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const store = useMemo(() => new SelectionStore(), []);
  const selection = useSelection(store);
  const listRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const next = await trashApi.overview();
      setData(next);
      store.retain(new Set(next.items.map((x) => x.node.id)));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [store]);

  useEffect(() => {
    void load();
  }, [load]);
  useReloadOn((e) => e.type === "connected" || e.type.startsWith("node."), () => void load());

  const items = useMemo(() => sortItems(data?.items ?? []), [data]);
  const order = useMemo(() => items.map((x) => x.node.id), [items]);
  const focusIndex = selection.focus === null ? -1 : order.indexOf(selection.focus);
  const targets = actionIds(order, selection.selected, selection.focus);
  const byId = useMemo(() => new Map(items.map((x) => [x.node.id, x])), [items]);

  function report(results: ItemResult[], done: (n: number) => string) {
    const { ok, failed } = outcome(results);
    if (ok > 0) toast(done(ok), { kind: "ok" });
    if (failed.length > 0) {
      toast(t("trash.failed", { n: failed.length, reason: errorText(failed[0]) }), { kind: "error" });
    }
  }

  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    // Al vanaf het begin: een live gebeurtenis (node.changed) laadt de lijst vaak eerder dan wij,
    // en dan verdwijnt de rij met de focus al; anders stond de focus tot ons eigen laden op <body>
    focusList();
    try {
      await fn();
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      focusList();
      await load();
    }
  }

  // Na een actie de focus terug in de lijst, op de rij die dan de tabstop is, pas als de nieuwe
  // inhoud er staat. Een requestAnimationFrame komt soms eerder: dan krijgt een rij die zo
  // verdwijnt (teruggezet, verwijderd) de focus, en valt die daarna naar <body>.
  const refocus = useRef(false);
  function focusList() {
    refocus.current = true;
  }
  useLayoutEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    const active = document.activeElement;
    if (active && active !== document.body && !listRef.current?.contains(active)) return; // elders
    const row = listRef.current?.querySelector<HTMLElement>('[role="row"][tabindex="0"]');
    row?.focus({ preventScroll: true });
  }, [data]);

  async function restore() {
    const ids = actionIds(order, store.getSnapshot().selected, store.getSnapshot().focus);
    if (ids.length === 0) return;
    await run(async () => {
      const r = await trashApi.restore(ids);
      report(r.results, (n) => t("trash.restored", { n }));
      store.clear();
    });
  }

  async function purge() {
    const ids = actionIds(order, store.getSnapshot().selected, store.getSnapshot().focus);
    if (ids.length === 0 || busy) return;
    const one = ids.length === 1 ? byId.get(ids[0]) : undefined;
    const yes = await confirm({
      title: one ? t("trash.purgeOneTitle", { name: one.node.name }) : t("trash.purgeTitle", { n: ids.length }),
      message: t("trash.purgeText"),
      confirm: t("trash.purge"),
      danger: true,
    });
    if (!yes) return;
    await run(async () => {
      const r = await trashApi.purge(ids);
      report(r.results, (n) => t("trash.purged", { n }));
      store.clear();
    });
  }

  async function empty() {
    if (!data || data.count === 0 || busy) return;
    const yes = await confirm({
      title: t("trash.emptyTitle"),
      message: t("trash.emptyConfirmText", {
        count: t("common.items", { n: data.count }),
        size: fmt.bytes(data.total_bytes),
      }),
      confirm: t("trash.emptyTrash"),
      danger: true,
    });
    if (!yes) return;
    await run(async () => {
      const r = await trashApi.empty();
      toast(t("trash.emptied", { n: r.purged }), { kind: "ok" });
      store.clear();
    });
  }

  useScope("trash");
  useShortcuts(
    [
      { combo: "enter", label: t("trash.keys.restore"), run: () => void restore(), when: () => targets.length > 0 },
      {
        combo: ["shift+delete", "shift+mod+backspace"],
        label: t("trash.keys.purge"),
        run: () => void purge(),
        when: () => targets.length > 0,
      },
      { combo: "mod+a", label: t("trash.keys.selectAll"), run: () => store.selectAll(order), when: () => order.length > 0 },
      {
        combo: "escape",
        label: t("trash.keys.clear"),
        run: () => store.clear(),
        when: () => store.getSnapshot().selected.size > 0,
      },
      { combo: "shift+l", label: t("trash.keys.emptyTrash"), run: () => void empty(), when: () => order.length > 0 },
    ],
    { scope: "trash" },
  );

  function onRowKey(e: KeyboardEvent<HTMLElement>, row: number) {
    // Vanaf de rij uit de selectie, niet die van het event: de focus verhuist pas in het
    // volgende frame, dus een toets snel na de vorige (Shift+↓ meteen na ↓) landt nog op de
    // oude rij
    const focus = store.getSnapshot().focus;
    const at = focus === null ? -1 : order.indexOf(focus);
    const i = at >= 0 ? at : row;
    if (e.key === " " && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      store.toggle(order[i]);
      return;
    }
    const move = keyToMove(e);
    if (!move || move === "left" || move === "right") return;
    const pageRows = Math.max(1, Math.floor(window.innerHeight / ROW_H) - 1);
    const next = moveIndex(i, move, order.length, 1, pageRows);
    if (next < 0 || next === i) return;
    e.preventDefault();
    const mods = { shift: e.shiftKey, toggle: e.ctrlKey || e.metaKey };
    store.set(keySelect(store.getSnapshot(), order, order[next], mods, order[i]));
    requestAnimationFrame(() => {
      const row = listRef.current?.querySelector<HTMLElement>(`[data-index="${next}"]`);
      row?.focus();
      row?.scrollIntoView({ block: "nearest" });
    });
  }

  function onRowClick(e: MouseEvent<HTMLElement>, id: string) {
    store.set(clickSelect(store.getSnapshot(), order, id, { shift: e.shiftKey, toggle: e.ctrlKey || e.metaKey }));
  }

  const n = selection.selected.size;
  const rovingIndex = focusIndex >= 0 ? focusIndex : 0;

  return (
    <main className="wide trash">
      <PageTitle>{t("trash.title")}</PageTitle>
      {error && <p role="alert">{error}</p>}
      {data && data.count === 0 && (
        <EmptyState icon="trash" title={t("trash.empty")}>
          {t("trash.emptyText")}
        </EmptyState>
      )}
      {data && data.count > 0 && (
        <>
          <div className="toolbar trash-toolbar">
            <span className="muted" aria-live="polite">
              {n > 0
                ? t("trash.selected", { n })
                : t("trash.total", { n: data.count, size: fmt.bytes(data.total_bytes) })}
            </span>
            <Button
              icon="restore"
              disabled={busy || targets.length === 0}
              aria-keyshortcuts="Enter"
              onClick={() => void restore()}
            >
              {n > 0 ? t("trash.restoreN", { n }) : t("trash.restore")}
            </Button>
            <Button
              icon="trash"
              variant="danger"
              disabled={busy || targets.length === 0}
              aria-keyshortcuts="Shift+Delete"
              onClick={() => void purge()}
            >
              {n > 0 ? t("trash.purgeN", { n }) : t("trash.purge")}
            </Button>
            <Button disabled={busy} aria-keyshortcuts="Shift+L" onClick={() => void empty()}>
              {t("trash.emptyTrash")}
            </Button>
          </div>
          <div ref={listRef}>
            <VirtualList<TrashItem>
              role="grid"
              ariaLabel={t("trash.list")}
              count={items.length}
              rowHeight={ROW_H}
              get={(i) => items[i]}
              getKey={(item, i) => item?.node.id ?? String(i)}
              keepIndex={focusIndex >= 0 ? focusIndex : null}
              header={
                <div role="row" aria-rowindex={1} className="trash-row trash-head">
                  <span role="columnheader" className="trash-check">
                    <span className="sr-only">{t("trash.keys.selectAll")}</span>
                  </span>
                  <span role="columnheader">{t("trash.name")}</span>
                  <span role="columnheader" className="wide-only">
                    {t("trash.wasIn")}
                  </span>
                  <span role="columnheader" className="trash-size">
                    {t("trash.size")}
                  </span>
                  <span role="columnheader" className="trash-date wide-only">
                    {t("trash.deleted")}
                  </span>
                </div>
              }
              renderRow={(item, i, style) =>
                item ? (
                  <Row
                    item={item}
                    index={i}
                    style={style}
                    selected={selection.selected.has(item.node.id)}
                    tabbable={i === rovingIndex}
                    onKeyDown={(e) => onRowKey(e, i)}
                    onClick={(e) => onRowClick(e, item.node.id)}
                    onToggle={() => store.toggle(item.node.id)}
                    onFocus={() => {
                      if (store.getSnapshot().focus !== item.node.id) {
                        const s = store.getSnapshot();
                        store.set({ ...s, focus: item.node.id });
                      }
                    }}
                  />
                ) : null
              }
            />
          </div>
        </>
      )}
    </main>
  );
}

function Row({
  item,
  index,
  style,
  selected,
  tabbable,
  onKeyDown,
  onClick,
  onToggle,
  onFocus,
}: {
  item: TrashItem;
  index: number;
  style: CSSProperties;
  selected: boolean;
  tabbable: boolean;
  onKeyDown(e: KeyboardEvent<HTMLElement>): void;
  onClick(e: MouseEvent<HTMLElement>): void;
  onToggle(): void;
  onFocus(): void;
}): JSX.Element {
  const n = item.node;
  const where = locationText(item);
  const icon = n.kind === "dir" ? "folder" : n.kind === "album" ? "album" : "file";
  return (
    <div
      role="row"
      aria-rowindex={index + 2}
      aria-selected={selected}
      data-index={index}
      tabIndex={tabbable ? 0 : -1}
      className={selected ? "trash-row selected" : "trash-row"}
      style={style}
      onKeyDown={onKeyDown}
      onClick={onClick}
      onFocus={onFocus}
    >
      <span role="gridcell" className="trash-check">
        <input
          type="checkbox"
          tabIndex={-1}
          checked={selected}
          aria-label={t("trash.select", { name: n.name })}
          onClick={(e) => e.stopPropagation()}
          onChange={onToggle}
        />
      </span>
      <span role="gridcell" className="trash-name">
        <Icon name={icon} size={20} />
        <span className="trash-label">{n.name}</span>
        {item.restore_to === "root" && (
          <span className="muted trash-root" title={t("trash.toRootHint")}>
            {t("trash.toRoot")}
          </span>
        )}
      </span>
      <span role="gridcell" className="wide-only trash-where">
        {where ?? "–"}
      </span>
      <span role="gridcell" className="trash-size">
        {fmt.bytes(item.size)}
      </span>
      <span role="gridcell" className="trash-date wide-only">
        {n.deleted_at ? fmt.dateTime(n.deleted_at) : ""}
      </span>
    </div>
  );
}
