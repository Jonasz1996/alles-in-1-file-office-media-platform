// Het raster: cellen van minstens --cell-min breed met een grote thumbnail, per rij van het
// raster gevirtualiseerd (ui/virtual). Een rij van het raster is role="row" met cellen
// (role="gridcell", data-index = de plek in de lijst); de cel met de focus is de enige tabstop.

import { type JSX, memo, useEffect, useRef } from "react";
import type { FileNode, Preview } from "../api";
import { t } from "../i18n";
import { Thumb } from "../Thumb";
import { useColumns, useVirtualRows } from "../ui/virtual";
import type { FileListProps } from "./FileList";
import { previewCache, useVisiblePreviews } from "./previews";
import { useRowEvents } from "./rowEvents";

/** Minimale breedte van een cel (--cell-min) en de ruimte ertussen. */
export const CELL_MIN = 160;
const GAP = 12;
/** De naam onder de thumbnail */
const NAME_H = 40;
const OVERSCAN = 2;
const AHEAD_ROWS = 12;

export type FileGridProps = Omit<FileListProps, "sort" | "sortBlocked" | "onSort" | "headerMenu" | "pathOf">;

interface CellProps {
  node: FileNode | undefined;
  index: number;
  selected: boolean;
  tabbable: boolean;
  cut: boolean;
  drop: boolean;
  preview: Preview | undefined;
}

const Cell = memo(function Cell({ node, index, selected, tabbable, cut, drop, preview }: CellProps) {
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
      <Thumb node={node} preview={preview} size={180} />
      <input
        type="checkbox"
        tabIndex={-1}
        data-check=""
        className="cell-checkbox"
        checked={selected}
        onChange={noop}
        aria-label={t("browser.row.select", { name: node.name })}
      />
      <button type="button" className="name" data-open="" tabIndex={-1}>
        {node.name}
      </button>
    </div>
  );
});

function noop(): void {}

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
  });
  if (p.scrollRef) p.scrollRef.current = (i) => rows.scrollToIndex(Math.floor(i / columns));
  const events = useRowEvents(grid, {
    ...p,
    columns,
    rowHeight,
    scrollToIndex: (i) => rows.scrollToIndex(Math.floor(i / columns)),
  });

  const wanted: (FileNode | undefined)[] = [];
  const last = Math.min(p.count, (rows.end + AHEAD_ROWS) * columns);
  for (let i = rows.start * columns; i < last; i++) wanted.push(p.get(i));
  useVisiblePreviews(wanted, `${rows.start}:${rows.end}:${columns}:${p.version}`);

  const { get, selected, isCut, dropId, focusIndex, onFocusRow, count } = p;
  const out: JSX.Element[] = [];
  const add = (r: number) => {
    const cells: JSX.Element[] = [];
    for (let c = 0; c < columns; c++) {
      const i = r * columns + c;
      if (i >= p.count) break;
      const node = get(i);
      cells.push(
        <Cell
          key={node?.id ?? `i${i}`}
          node={node}
          index={i}
          selected={node ? selected.has(node.id) : false}
          tabbable={i === focusIndex || (focusIndex < 0 && i === 0)}
          cut={node ? (isCut?.(node.id) ?? false) : false}
          drop={!!node && node.id === dropId}
          preview={node && node.kind === "file" ? previewCache.get(node.id, node.etag) : undefined}
        />,
      );
    }
    out.push(
      <div
        key={`r${r}`}
        role="row"
        aria-rowindex={r + 1}
        className="file-grid-row"
        style={{
          transform: `translateY(${rows.offsetOf(r)}px)`,
          height: rowHeight - GAP,
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        }}
      >
        {cells}
      </div>,
    );
  };
  for (let r = rows.start; r < rows.end; r++) add(r);
  if (rows.extra !== null) add(rows.extra);

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
