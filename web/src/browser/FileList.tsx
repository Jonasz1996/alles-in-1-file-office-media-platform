// De lijst van een map (of zoekresultaten): een grid (role="grid") met een kopregel en alleen de
// zichtbare rijen in de DOM (ui/virtual), ook bij 10.000 items. Eén tabstop (roving focus); de
// rij met de focus blijft gemount (keepIndex), ook buiten beeld. Rijen die nog niet geladen
// zijn, staan er al (aria-rowcount klopt meteen) als lege rij. Alle gebeurtenissen gaan via het
// element eromheen (rowEvents.ts): een rij is een memo zonder eigen functies.

import { type JSX, memo, type ReactNode, useEffect, useRef } from "react";
import type { FileNode, Preview } from "../api";
import { fmt, t } from "../i18n";
import { isShared } from "../sharing";
import { TagChips } from "../Tags";
import { Thumb } from "../Thumb";
import { Icon } from "../ui/Icon";
import { useVirtualRows } from "../ui/virtual";
import { previewCache, useWindowPreviews } from "./previews";
import { type RowCallbacks, useRowEvents, useScrollingClass } from "./rowEvents";
import { SlotAssigner, useDeferredRange, useNextStep } from "./slots";
import type { Sort, SortKey } from "./sort";

/** De hoogte van een rij (--row-h in tokens.css). */
export const ROW_H = 44;
/** Zoveel rijen boven en onder het beeld staan er ook (scrollen zonder gaten). */
const OVERSCAN = 8;
/** Hooguit zoveel nieuwe rijen per frame (slots.ts): een wielstap brengt er een stuk of 9, een
 * PageDown een beeld vol (in twee gelijke frames in plaats van een grote en een kleine). */
const ROW_BUDGET = 10;

export interface FileListProps extends Omit<RowCallbacks, "columns" | "rowHeight" | "scrollToIndex"> {
  get(i: number): FileNode | undefined;
  /** Verandert als de nodes veranderen (store-versie) */
  version: number;
  selected: ReadonlySet<string>;
  isCut?(id: string): boolean;
  /** De map waar iets op losgelaten kan worden */
  dropId?: string | null;
  sort?: Sort;
  /** Waarom sorteren (nog) niet kan; null = het kan */
  sortBlocked?: string | null;
  onSort?(key: SortKey): void;
  /** Het menu ⋯ in de kopregel */
  headerMenu?: ReactNode;
  ariaLabel: string;
  /** Een extra kolom met de map (zoekresultaten) */
  pathOf?(node: FileNode): ReactNode;
  /** De lijst kan nu zelf naar rij i scrollen (voor de ouder) */
  scrollRef?: { current: ((i: number) => void) | null };
}

interface RowProps {
  node: FileNode | undefined;
  index: number;
  top: number;
  selected: boolean;
  tabbable: boolean;
  cut: boolean;
  drop: boolean;
  preview: Preview | undefined;
  /** Het antwoord over de preview is er nog niet (Thumb) */
  pending: boolean;
  path: ReactNode;
  showPath: boolean;
}

const Row = memo(function Row({
  node,
  index,
  top,
  selected,
  tabbable,
  cut,
  drop,
  preview,
  pending,
  path,
  showPath,
}: RowProps) {
  // top in plaats van transform: zonder transform geen eigen transform-knoop per rij bij het
  // tekenen
  const style = { top };
  if (!node) {
    return (
      <div
        role="row"
        aria-rowindex={index + 2}
        data-index={index}
        tabIndex={tabbable ? 0 : -1}
        className="file-row placeholder"
        style={style}
      >
        <div role="gridcell" className="cell-name">
          <span className="placeholder-bar" />
          <span className="sr-only">{t("browser.row.loading")}</span>
        </div>
      </div>
    );
  }
  const classes = ["file-row", selected && "selected", cut && "cut", drop && "drop-target"].filter(Boolean).join(" ");
  return (
    <div
      role="row"
      aria-rowindex={index + 2}
      aria-selected={selected}
      data-index={index}
      tabIndex={tabbable ? 0 : -1}
      className={classes}
      style={style}
      draggable
    >
      <div role="gridcell" className="cell-name">
        {/* Een vinkje zonder <input>: goedkoper bij het scrollen; de rij zelf heeft aria-selected */}
        <span
          role="checkbox"
          className="row-check"
          data-check=""
          aria-checked={selected}
          aria-label={t("browser.row.select", { name: node.name })}
        />
        <Thumb node={node} preview={preview} pending={pending} size={32} />
        {/* De naam: een klik opent (rowEvents.ts); met het toetsenbord Enter op de rij */}
        <button type="button" className="name" data-open="" tabIndex={-1}>
          {node.name}
        </button>
        {node.shared && (
          <span className="shared-mark" title={t("browser.row.sharedTitle")}>
            {t("browser.row.shared")}
          </span>
        )}
        {isShared(node) && node.owner && (
          <span className="owner-note">{t("browser.row.owner", { name: node.owner.display_name })}</span>
        )}
        <TagChips tags={node.tags} />
        {/* Plek voor een status per rij (fase 10, bijv. omzetten); leeg = onzichtbaar */}
        <span className="row-status" data-row-status="" />
      </div>
      {showPath && (
        <div role="gridcell" className="cell-path">
          {path}
        </div>
      )}
      <div role="gridcell" className="cell-size">
        {node.kind === "file" ? fmt.bytes(node.size) : ""}
      </div>
      <div role="gridcell" className="cell-date">
        {dateText(node.modified_at)}
      </div>
      <div role="gridcell" className="cell-more">
        <button
          type="button"
          className="icon-button ghost more-dots"
          data-menu=""
          tabIndex={-1}
          aria-haspopup="menu"
          aria-label={t("browser.row.actions", { name: node.name })}
        />
      </div>
    </div>
  );
});

/** De datum als tekst, één keer per waarde (een lijst van 10.000 rijen met vaak dezelfde tijden). */
const dates = new Map<string, string>();
function dateText(iso: string): string {
  let text = dates.get(iso);
  if (text === undefined) {
    if (dates.size > 5000) dates.clear();
    text = fmt.dateTime(iso);
    dates.set(iso, text);
  }
  return text;
}

function HeaderCell({
  label,
  sortKey,
  sort,
  blocked,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  sort?: Sort;
  blocked?: string | null;
  onSort?(key: SortKey): void;
  className: string;
}): JSX.Element {
  const active = sort?.key === sortKey;
  const ariaSort = active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined;
  return (
    <div role="columnheader" aria-sort={ariaSort} className={className}>
      {onSort ? (
        <button
          type="button"
          className="sort-button"
          aria-disabled={blocked ? true : undefined}
          title={blocked ?? undefined}
          onClick={() => !blocked && onSort(sortKey)}
        >
          {label}
          {active && <Icon name={sort.dir === "asc" ? "sort-asc" : "sort-desc"} size={16} />}
        </button>
      ) : (
        label
      )}
    </div>
  );
}

export function FileList(p: FileListProps): JSX.Element {
  const grid = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const rows = useVirtualRows({
    count: p.count,
    rowHeight: ROW_H,
    overscan: OVERSCAN,
    listRef: body,
    keepIndex: p.focusIndex >= 0 ? p.focusIndex : null,
    transition: true,
  });
  if (p.scrollRef) p.scrollRef.current = (i) => rows.scrollToIndex(i);
  useScrollingClass(grid);
  const events = useRowEvents(grid, {
    ...p,
    columns: 1,
    rowHeight: ROW_H,
    scrollToIndex: (i) => rows.scrollToIndex(i),
  });

  // Previews per blok: wat zichtbaar is eerst, dan wat eraan komt (ook bij snel scrollen)
  useWindowPreviews(p.get, rows.start, rows.end, p.count, p.version);

  const { get, selected, isCut, dropId, focusIndex, pathOf } = p;
  const showPath = !!pathOf;
  const out: JSX.Element[] = [];
  const add = (i: number, key: string) => {
    const node = get(i);
    out.push(
      <Row
        key={key}
        node={node}
        index={i}
        top={rows.offsetOf(i)}
        selected={node ? selected.has(node.id) : false}
        tabbable={i === focusIndex || (focusIndex < 0 && i === 0)}
        cut={node ? (isCut?.(node.id) ?? false) : false}
        drop={!!node && node.id === dropId}
        preview={node && node.kind === "file" ? previewCache.get(node.id, node.etag) : undefined}
        pending={!!node && node.kind === "file" && previewCache.pending(node.id, node.etag)}
        path={node && pathOf ? pathOf(node) : null}
        showPath={showPath}
      />,
    );
  };
  // Rijen op vaste plekken: bij het scrollen worden bestaande elementen hergebruikt (slots.ts)
  const slotter = useRef<SlotAssigner | null>(null);
  slotter.current ??= new SlotAssigner();
  const range = useDeferredRange(rows.start, rows.end, p.count, focusIndex >= 0 ? focusIndex : null);
  for (const slot of slotter.current.assign(range.start, range.end, range.extra, ROW_BUDGET)) add(slot.index, slot.key);
  useNextStep(slotter.current.pending);

  // Een kortere lijst (filter, verwijderd): de focus blijft binnen de lijst
  const { onFocusRow, count } = p;
  useEffect(() => {
    if (count > 0 && focusIndex >= count) onFocusRow(count - 1);
  }, [count, focusIndex, onFocusRow]);

  return (
    <div
      ref={grid}
      role="grid"
      aria-label={p.ariaLabel}
      aria-rowcount={p.count + 1}
      aria-multiselectable="true"
      className={`file-list${showPath ? " with-path" : ""}`}
      data-view="list"
      {...events}
    >
      <div role="rowgroup" className="file-head-group">
        <div role="row" aria-rowindex={1} className="file-row file-head">
          <HeaderCell
            label={t("browser.columns.name")}
            sortKey="name"
            sort={p.sort}
            blocked={p.sortBlocked}
            onSort={p.onSort}
            className="cell-name"
          />
          {showPath && (
            <div role="columnheader" className="cell-path">
              {t("browser.columns.path")}
            </div>
          )}
          <HeaderCell
            label={t("browser.columns.size")}
            sortKey="size"
            sort={p.sort}
            blocked={p.sortBlocked}
            onSort={p.onSort}
            className="cell-size"
          />
          <HeaderCell
            label={t("browser.columns.modified")}
            sortKey="modified"
            sort={p.sort}
            blocked={p.sortBlocked}
            onSort={p.onSort}
            className="cell-date"
          />
          <div role="columnheader" className="cell-more">
            {p.headerMenu}
          </div>
        </div>
      </div>
      <div ref={body} role="rowgroup" className="file-body" style={{ height: rows.totalHeight }}>
        {out}
      </div>
    </div>
  );
}
