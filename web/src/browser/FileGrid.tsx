// Het raster: cellen van minstens --cell-min breed met een grote thumbnail, per rij van het
// raster gevirtualiseerd (ui/virtual). Een rij van het raster is role="row" met cellen
// (role="gridcell", data-index = de plek in de lijst); de cel met de focus is de enige tabstop.

import { type JSX, memo, useEffect, useRef } from "react";
import type { FileNode, Preview } from "../api";
import { t } from "../i18n";
import { Thumb } from "../Thumb";
import { useColumns, useVirtualRows } from "../ui/virtual";
import type { FileListProps } from "./FileList";
import { previewCache, useWindowPreviews } from "./previews";
import { useRowEvents, useScrollingClass } from "./rowEvents";
import { SlotAssigner, useDeferredRange, useNextStep } from "./slots";

/** Minimale breedte van een cel (--cell-min) en de ruimte ertussen. */
export const CELL_MIN = 160;
const GAP = 12;
/** De naam onder de thumbnail */
const NAME_H = 40;
const OVERSCAN = 2;
/** Hooguit zoveel nieuwe cellen per frame (in hele rijen van het raster; slots.ts) */
const CELL_BUDGET = 18;

export type FileGridProps = Omit<FileListProps, "sort" | "sortBlocked" | "onSort" | "headerMenu" | "pathOf">;

interface CellProps {
  node: FileNode | undefined;
  index: number;
  selected: boolean;
  tabbable: boolean;
  cut: boolean;
  drop: boolean;
  preview: Preview | undefined;
  /** Het antwoord over de preview is er nog niet (Thumb) */
  pending: boolean;
}

const Cell = memo(function Cell({ node, index, selected, tabbable, cut, drop, preview, pending }: CellProps) {
  if (!node) {
    return (
      <div role="gridcell" data-index={index} tabIndex={tabbable ? 0 : -1} className="file-cell placeholder">
        <span className="sr-only">{t("browser.row.loading")}</span>
      </div>
    );
  }
  const classes = ["file-cell", selected && "selected", cut && "cut", drop && "drop-target"].filter(Boolean).join(" ");
  return (
    <div
      role="gridcell"
      aria-selected={selected}
      data-index={index}
      tabIndex={tabbable ? 0 : -1}
      className={classes}
      draggable
    >
      <Thumb node={node} preview={preview} pending={pending} size={180} />
      <span
        role="checkbox"
        className="row-check cell-checkbox"
        data-check=""
        aria-checked={selected}
        aria-label={t("browser.row.select", { name: node.name })}
      />
      <button type="button" className="name" data-open="" tabIndex={-1}>
        {node.name}
      </button>
    </div>
  );
});

export function FileGrid(p: FileGridProps): JSX.Element {
  const grid = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const { columns, cellWidth } = useColumns(grid, CELL_MIN, GAP);
  const rowHeight = Math.round(cellWidth) + NAME_H + GAP;
  const rowCount = Math.ceil(p.count / columns);
  const focusRowIndex = p.focusIndex >= 0 ? Math.floor(p.focusIndex / columns) : null;
  const rows = useVirtualRows({
    count: rowCount,
    rowHeight,
    overscan: OVERSCAN,
    listRef: body,
    keepIndex: focusRowIndex,
    transition: true,
  });
  if (p.scrollRef) p.scrollRef.current = (i) => rows.scrollToIndex(Math.floor(i / columns));
  useScrollingClass(grid);
  const events = useRowEvents(grid, {
    ...p,
    columns,
    rowHeight,
    scrollToIndex: (i) => rows.scrollToIndex(Math.floor(i / columns)),
  });

  useWindowPreviews(p.get, rows.start * columns, rows.end * columns, p.count, p.version);

  const { get, selected, isCut, dropId, focusIndex, onFocusRow, count } = p;
  const out: JSX.Element[] = [];
  const add = (r: number, key: string) => {
    const cells: JSX.Element[] = [];
    for (let c = 0; c < columns; c++) {
      const i = r * columns + c;
      if (i >= p.count) break;
      const node = get(i);
      cells.push(
        <Cell
          key={`c${c}`}
          node={node}
          index={i}
          selected={node ? selected.has(node.id) : false}
          tabbable={i === focusIndex || (focusIndex < 0 && i === 0)}
          cut={node ? (isCut?.(node.id) ?? false) : false}
          drop={!!node && node.id === dropId}
          preview={node && node.kind === "file" ? previewCache.get(node.id, node.etag) : undefined}
          pending={!!node && node.kind === "file" && previewCache.pending(node.id, node.etag)}
        />,
      );
    }
    out.push(
      <div
        key={key}
        role="row"
        aria-rowindex={r + 1}
        className="file-grid-row"
        style={{
          top: rows.offsetOf(r),
          height: rowHeight - GAP,
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        }}
      >
        {cells}
      </div>,
    );
  };
  // Rijen van het raster op vaste plekken (slots.ts): bestaande elementen worden hergebruikt
  const slotter = useRef<SlotAssigner | null>(null);
  slotter.current ??= new SlotAssigner();
  const range = useDeferredRange(rows.start, rows.end, rowCount, focusRowIndex);
  const budget = Math.max(1, Math.floor(CELL_BUDGET / columns));
  for (const slot of slotter.current.assign(range.start, range.end, range.extra, budget)) add(slot.index, slot.key);
  useNextStep(slotter.current.pending);

  useEffect(() => {
    if (count > 0 && focusIndex >= count) onFocusRow(count - 1);
  }, [count, focusIndex, onFocusRow]);

  return (
    <div
      ref={grid}
      role="grid"
      aria-label={p.ariaLabel}
      aria-rowcount={rowCount}
      aria-multiselectable="true"
      className="file-grid"
      data-view="grid"
      data-columns={columns}
      {...events}
    >
      <div ref={body} className="file-grid-body" style={{ height: rows.totalHeight }}>
        {out}
      </div>
    </div>
  );
}
