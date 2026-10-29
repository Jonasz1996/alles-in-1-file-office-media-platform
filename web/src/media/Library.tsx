// /media/bibliotheek/:id (fase 5, M2): de inhoud van een bibliotheek als raster van posters, met
// sorteren (?sort=) en het soort (?type=). Het raster is per rij gevirtualiseerd over het venster,
// zoals de fototijdlijn van fase 3 (ui/virtual): één tabstop, de pijltjes, Home/End en
// PageUp/PageDown verplaatsen de focus. De volgende pagina (cursor) komt als de rijen in beeld bij
// het eind komen. media.item en media.library van deze bibliotheek laden de lijst opnieuw.
//
// MediaCard (de kaart met poster, titel en voortgang) gebruikt ook de start (/media).

import { type JSX, memo, useCallback, useEffect, useRef, useState } from "react";
import { useReloadOn } from "../events";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import { type RouteProps, useQueryParam, useRestoreScroll } from "../router";
import NotFound from "../shellPages";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { toast } from "../ui/toast";
import { useColumns, useRovingFocus, useVirtualRows } from "../ui/virtual";
import { listItems, scanLibrary } from "./api";
import { MediaGate } from "./Gate";
import {
  CARD_GAP,
  CARD_MIN,
  cardHref,
  cardKey,
  episodeCode,
  gridRowHeight,
  mergePage,
  PAGE_SIZE,
  progressFraction,
  RELOAD_MAX,
  wantsMore,
} from "./grid";
import { MEDIA_HOME, MUSIC_HOME, mediaStatus, useMediaStatus } from "./index";
import {
  affectsLibrary,
  kindIcon,
  type ListType,
  parseSort,
  parseType,
  SORTS,
  type Sort,
  scanBusy,
  shownType,
  typesFor,
} from "./library";
import type { Card, Library } from "./types";
import "./media.css";

// --- de kaart ---

export interface MediaCardProps {
  card: Card;
  /** De plek in de lijst (data-index, voor de roving focus) */
  index: number;
  /** De enige tabstop van de lijst */
  tabbable: boolean;
  onFocusCard?(index: number): void;
}

/** Een kaart: de poster (of een icoon), de titel, het jaar of de aflevering en de voortgang. Een
 * link naar het item of de serie; zonder bestand (weg) alleen tekst. */
export const MediaCard = memo(function MediaCard({ card, index, tabbable, onFocusCard }: MediaCardProps) {
  const href = cardHref(card);
  const done = progressFraction(card.progress);
  const code = card.kind === "episode" ? episodeCode(card.season, card.episode) : "";
  const sub = [code || (card.year ? String(card.year) : ""), card.watched ? t("media.card.watched") : ""]
    .filter(Boolean)
    .join(" · ");
  const body = (
    <>
      <span className="media-poster" aria-hidden="true">
        {card.poster ? (
          <img src={card.poster} alt="" loading="lazy" decoding="async" />
        ) : (
          <Icon name={card.kind === "track" ? "audio" : "video"} size={32} />
        )}
        {done > 0 && (
          <span className="media-progress">
            <span style={{ transform: `scaleX(${done})` }} />
          </span>
        )}
      </span>
      <span className="media-card-title">{card.title}</span>
      {sub && <span className="media-card-sub muted">{sub}</span>}
      {done > 0 && <span className="sr-only">{t("media.card.progress", { percent: fmt.percent(done) })}</span>}
    </>
  );
  const common = {
    className: "media-card",
    "data-index": index,
    tabIndex: tabbable ? 0 : -1,
    onFocus: () => onFocusCard?.(index),
  };
  if (!href) {
    return (
      <span {...common} aria-disabled="true">
        {body}
      </span>
    );
  }
  return (
    <Link to={href} {...common}>
      {body}
    </Link>
  );
});

// --- de lijst ---

interface ListState {
  key: string;
  cards: Card[];
  next: string | null;
  loading: boolean;
  /** De eerste pagina is er (of gaf een fout) */
  loaded: boolean;
  error: string | null;
}

function emptyList(key: string): ListState {
  return { key, cards: [], next: null, loading: true, loaded: false, error: null };
}

/** De kaarten van een bibliotheek, pagina voor pagina. Een antwoord op een oudere vraag (een
 * andere sortering, of intussen opnieuw geladen) telt niet meer. */
function useCards(lib: Library, sort: Sort, type: ListType | undefined) {
  const key = `${lib.id}|${sort}|${type ?? ""}`;
  const [state, setState] = useState<ListState>(() => emptyList(key));
  const gen = useRef(0);
  const latest = useRef(state);
  useEffect(() => {
    latest.current = state;
  });

  const first = useCallback(
    async (limit: number) => {
      const g = ++gen.current;
      try {
        const page = await listItems(lib.id, { sort, type, limit });
        if (g !== gen.current) return;
        setState({ key, cards: page.items, next: page.next, loading: false, loaded: true, error: null });
      } catch (e) {
        if (g !== gen.current) return;
        setState((old) => ({ ...old, key, loading: false, loaded: true, error: errorText(e) }));
      }
    },
    [lib.id, sort, type, key],
  );

  useEffect(() => {
    setState(emptyList(key));
    void first(PAGE_SIZE);
  }, [first, key]);

  const more = useCallback(async () => {
    const s = latest.current;
    if (!s.next || s.loading || s.key !== key) return;
    const g = gen.current;
    latest.current = { ...s, loading: true };
    setState((old) => ({ ...old, loading: true, error: null }));
    try {
      const page = await listItems(lib.id, { sort, type, cursor: s.next, limit: PAGE_SIZE });
      if (g !== gen.current) return;
      setState((old) => ({ ...old, cards: mergePage(old.cards, page), next: page.next, loading: false }));
    } catch (e) {
      if (g !== gen.current) return;
      setState((old) => ({ ...old, loading: false, error: errorText(e) }));
    }
  }, [lib.id, sort, type, key]);

  /** Opnieuw laden (live): zoveel als er al stond, tot RELOAD_MAX; de rest komt weer bij scrollen. */
  const reload = useCallback(() => {
    void first(Math.min(RELOAD_MAX, Math.max(PAGE_SIZE, latest.current.cards.length)));
  }, [first]);

  return { ...(state.key === key ? state : emptyList(key)), more, reload };
}

interface GridProps {
  cards: Card[];
  label: string;
  /** De rijen tot endRow zijn in beeld, bij zoveel kolommen */
  onRows(endRow: number, columns: number): void;
}

function CardGrid({ cards, label, onRows }: GridProps): JSX.Element {
  const grid = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const { columns, cellWidth } = useColumns(grid, CARD_MIN, CARD_GAP);
  const rowHeight = gridRowHeight(cellWidth);
  const rowCount = Math.ceil(cards.length / columns);
  const [focus, setFocus] = useState(0);
  const current = Math.min(focus, Math.max(0, cards.length - 1));
  const rows = useVirtualRows({
    count: rowCount,
    rowHeight,
    overscan: 2,
    listRef: body,
    keepIndex: cards.length ? Math.floor(current / columns) : null,
    transition: true,
  });

  useEffect(() => {
    onRows(rows.end, columns);
  }, [rows.end, columns, cards.length, onRows]);

  const element = useCallback(
    (i: number) => body.current?.querySelector<HTMLElement>(`[data-index="${i}"]`) ?? null,
    [],
  );
  const onKeyDown = useRovingFocus({
    count: cards.length,
    index: current,
    setIndex: setFocus,
    columns,
    scrollToIndex: (i) => rows.scrollToIndex(Math.floor(i / columns)),
    getElement: element,
  });

  const out: JSX.Element[] = [];
  const add = (r: number) => {
    const cells: JSX.Element[] = [];
    for (let c = 0; c < columns; c++) {
      const i = r * columns + c;
      if (i >= cards.length) break;
      cells.push(
        <div role="gridcell" key={cardKey(cards[i])} className="media-cell">
          <MediaCard card={cards[i]} index={i} tabbable={i === current} onFocusCard={setFocus} />
        </div>,
      );
    }
    out.push(
      <div
        key={r}
        role="row"
        aria-rowindex={r + 1}
        className="media-grid-row"
        style={{
          transform: `translateY(${rows.offsetOf(r)}px)`,
          height: rowHeight - CARD_GAP,
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        }}
      >
        {cells}
      </div>,
    );
  };
  for (let r = rows.start; r < rows.end; r++) add(r);
  if (rows.extra !== null) add(rows.extra);

  return (
    <div
      ref={grid}
      role="grid"
      aria-label={label}
      aria-rowcount={rowCount}
      className="media-grid"
      data-columns={columns}
      onKeyDown={onKeyDown}
    >
      <div ref={body} className="media-grid-body" style={{ height: rows.totalHeight }}>
        {out}
      </div>
    </div>
  );
}

// --- de pagina ---

function LibraryView({ lib }: { lib: Library }): JSX.Element {
  const [sortParam, setSortParam] = useQueryParam("sort");
  const [typeParam, setTypeParam] = useQueryParam("type");
  const sort = parseSort(sortParam);
  const type = parseType(lib.kind, typeParam);
  const types = typesFor(lib.kind);
  const list = useCards(lib, sort, type);
  const { more, reload } = list;
  const [scanning, setScanning] = useState(false);

  useReloadOn((e) => affectsLibrary(e, lib.id), reload, 500);
  useRestoreScroll(list.loaded);

  // De laatste stand voor onRows (die verandert dan niet bij elke render)
  const state = useRef({ loaded: 0, next: null as string | null, busy: true });
  useEffect(() => {
    state.current = { loaded: list.cards.length, next: list.next, busy: list.loading || list.error !== null };
  });
  const lastRows = useRef<[number, number]>([0, 1]);
  const onRows = useCallback(
    (endRow: number, columns: number) => {
      lastRows.current = [endRow, columns];
      const s = state.current;
      if (wantsMore(s.loaded, endRow, columns, s.next, s.busy)) void more();
    },
    [more],
  );
  // Een pagina erbij kan nog steeds te kort zijn voor het scherm: dan meteen de volgende
  useEffect(() => {
    if (!list.loading && list.error === null) onRows(...lastRows.current);
  }, [list.loading, list.error, list.cards.length, onRows]);

  const rescan = useCallback(async () => {
    setScanning(true);
    try {
      await scanLibrary(lib.id);
      toast(t("media.scan.started", { name: lib.name }));
      void mediaStatus.load();
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setScanning(false);
    }
  }, [lib.id, lib.name]);

  const shown = shownType(lib.kind, type);
  return (
    <main className="wide media-page">
      <PageTitle
        actions={
          <>
            <Link to={MEDIA_HOME} className="btn">
              {t("media.title")}
            </Link>
            {lib.mine && (
              <Button icon="rotate" busy={scanning} disabled={scanBusy(lib.scan_state)} onClick={() => void rescan()}>
                {t("media.scan.again")}
              </Button>
            )}
          </>
        }
      >
        {lib.name}
      </PageTitle>
      <p className="media-meta muted">
        <Icon name={kindIcon(lib.kind)} size={16} />
        <span>{t(`media.kinds.${lib.kind}`)}</span>
        <span>{t("media.count", { n: lib.item_count })}</span>
        {!lib.mine && <span>{t("media.shared")}</span>}
        <span role="status" className={lib.scan_state === "failed" ? "media-bad" : undefined}>
          {lib.scan_state !== "idle" ? t(`media.scan.${lib.scan_state}`) : ""}
        </span>
      </p>
      {lib.kind === "music" && (
        <p className="media-note">
          {t("media.list.musicHint")} <Link to={MUSIC_HOME}>{t("media.list.toMusic")}</Link>
        </p>
      )}
      <div className="media-toolbar">
        <label>
          <span>{t("media.list.sort")}</span>
          <select
            value={sort}
            onChange={(e) => setSortParam(e.target.value === "title" ? null : e.target.value, { replace: true })}
          >
            {SORTS.map((s) => (
              <option key={s} value={s}>
                {t(`media.list.sorts.${s}`)}
              </option>
            ))}
          </select>
        </label>
        {types.length > 1 && shown && (
          <label>
            <span>{t("media.list.type")}</span>
            <select
              value={shown}
              onChange={(e) => setTypeParam(e.target.value === types[0] ? null : e.target.value, { replace: true })}
            >
              {types.map((x) => (
                <option key={x} value={x}>
                  {t(`media.list.types.${x}`)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {!list.loaded && <Skeleton lines={3} />}
      {list.loaded && list.cards.length === 0 && !list.error && (
        <EmptyState icon={kindIcon(lib.kind)} title={t("media.list.emptyTitle")}>
          <p>{t("media.list.emptyText")}</p>
        </EmptyState>
      )}
      {list.cards.length > 0 && (
        <CardGrid cards={list.cards} label={t("media.list.label", { name: lib.name })} onRows={onRows} />
      )}
      {list.loaded && list.loading && list.cards.length > 0 && (
        <p className="muted" role="status">
          {t("media.list.more")}
        </p>
      )}
      {list.error && (
        <div role="alert" className="media-error">
          <p>{list.error}</p>
          <Button onClick={() => (list.cards.length ? void more() : reload())}>{t("common.retry")}</Button>
        </div>
      )}
    </main>
  );
}

function LibraryPage({ params }: RouteProps): JSX.Element {
  const id = params.id ?? "";
  const status = useMediaStatus();
  const lib = status?.libraries.find((l) => l.id === id) ?? null;
  // Niet in de status: misschien net gemaakt of net gedeeld. Eén keer opnieuw vragen
  const [asked, setAsked] = useState<string | null>(null);
  const missing = lib === null;
  useEffect(() => {
    if (!missing) return;
    let live = true;
    void mediaStatus.load().then(() => live && setAsked(id));
    return () => {
      live = false;
    };
  }, [id, missing]);
  if (lib) return <LibraryView key={lib.id} lib={lib} />;
  if (asked !== id) return <main className="center">{t("common.loading")}</main>;
  return <NotFound params={params} />;
}

export default function LibraryRoute({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <LibraryPage params={params} />}</MediaGate>;
}
