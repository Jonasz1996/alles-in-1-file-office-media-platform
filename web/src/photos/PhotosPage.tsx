// De fototijdlijn (/fotos): al je eigen foto's en video's op opnamedatum, de nieuwste eerst, per
// maand. De maanden en hun aantallen komen eerst (/photos/months), zodat de volle hoogte en de
// scrubber meteen kloppen; de foto's van een maand pas als hij in beeld komt.

import { type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { crossApi, isNotBuilt } from "../crossApi";
import { useReloadOn } from "../events";
import { errorText, t } from "../i18n";
import { Link } from "../links";
import { markReady } from "../perf";
import { type RouteProps, useRestoreScroll } from "../router";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { useScope, useShortcuts } from "../ui/keys";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { toast } from "../ui/toast";
import { useColumns } from "../ui/virtual";
import AddToAlbumDialog from "./AddToAlbum";
import { type MonthCount, monthLabel, photosApi } from "./api";
import { downloadZip, SelectionBar, usePhotoBrowser, useStoreVersion } from "./browse";
import { GAP, type GridControl, MIN_CELL, PhotoGrid } from "./Grid";
import { Scrubber } from "./Scrubber";
import { SectionStore } from "./store";
import { buildLayout } from "./timeline";
import "./photos.css";

const HEADER_H = 48;
/** Zoveel tegelijk naar de prullenbak zonder de bulk-API (P2) */
const ONE_BY_ONE = 200;

export default function PhotosPage(_: RouteProps): JSX.Element {
  useScope("photos");
  const [months, setMonths] = useState<MonthCount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState<string[] | null>(null);
  const monthsRef = useRef<MonthCount[]>([]);
  const wrap = useRef<HTMLDivElement>(null);
  const control = useRef<GridControl | null>(null);
  const { columns, cellWidth } = useColumns(wrap, MIN_CELL, GAP);
  const rowHeight = Math.max(MIN_CELL / 2, Math.round(cellWidth)) + GAP;

  const store = useMemo(
    () =>
      new SectionStore((s, after) => {
        const m = monthsRef.current[s];
        return photosApi.page(m.month, after);
      }),
    [],
  );
  const version = useStoreVersion(store);

  const loadMonths = useCallback(async () => {
    try {
      const got = (await photosApi.months()).months;
      const before = new Map(monthsRef.current.map((m, s) => [m.month, s]));
      monthsRef.current = got;
      store.setSections(
        got.map((m) => m.count),
        undefined,
        (s) => before.get(got[s].month) ?? -1,
      );
      setMonths(got);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [store]);

  useEffect(() => {
    store.onMismatch = () => void loadMonths();
    store.onError = (e) => setError(errorText(e));
    void loadMonths();
  }, [store, loadMonths]);

  // Live: een upload, iets weg of terug, of een thumbnail die klaar is
  useReloadOn(
    (e) => e.type === "connected" || e.type.startsWith("node.") || e.type === "preview.ready",
    () => void loadMonths(),
    500,
  );

  const counts = useMemo(() => (months ?? []).map((m) => m.count), [months]);
  const layout = useMemo(() => buildLayout(counts, columns, rowHeight, HEADER_H), [counts, columns, rowHeight]);
  const labels = useMemo(() => (months ?? []).map((m) => monthLabel(m.month)), [months]);
  // Een nieuwe functie bij elke nieuwe versie van de store; de rijen van een maand tekenen
  // alleen opnieuw als juist die maand veranderde (een nieuwe array)
  const section = useMemo(() => (version >= 0 ? (s: number) => store.section(s) : () => undefined), [store, version]);
  const monthKeys = useMemo(() => (months ?? []).map((m) => m.month), [months]);
  const listTop = useCallback(() => control.current?.listTop() ?? 0, []);
  const onVisible = useCallback((sections: number[]) => store.want(sections), [store]);
  const fetchIds = photosApi.ids;

  const b = usePhotoBrowser({ store, layout, fetchIds, control, scope: "photos" });
  const selected = b.selection.ids;
  const ids = useMemo(() => [...selected], [selected]);

  useRestoreScroll(months !== null);
  useEffect(() => {
    if (months && (months.length === 0 || store.isLoaded(0))) markReady("photos");
  }, [months, store, version]);

  async function trash(list: string[]) {
    if (list.length === 0) return;
    let done = 0;
    try {
      const r = await crossApi.trash(list);
      done = r.results.filter((x) => x.ok).length;
    } catch (e) {
      if (!isNotBuilt(e) || list.length > ONE_BY_ONE) {
        toast(isNotBuilt(e) ? t("photos.trashTooMany", { max: ONE_BY_ONE }) : errorText(e), { kind: "error" });
        return;
      }
      // Nog zonder de bulk-API: één voor één
      for (const id of list) {
        try {
          await api.trash(id);
          done++;
        } catch (err) {
          toast(errorText(err), { kind: "error" });
        }
      }
    }
    b.clear();
    void loadMonths();
    if (done)
      toast(t("photos.trashed", { n: done }), {
        kind: "ok",
        action: {
          label: t("photos.undo"),
          run: () => void crossApi.restore(list).then(loadMonths, (e: unknown) => toast(errorText(e))),
        },
      });
  }

  const some = () => selected.size > 0;
  useShortcuts(
    [
      { combo: "a", label: t("photos.keys.addToAlbum"), run: () => setAdding(ids), when: some },
      { combo: "d", label: t("photos.keys.download"), run: () => void downloadZip(ids, t("photos.title")), when: some },
      { combo: ["delete", "mod+backspace"], label: t("photos.keys.trash"), run: () => void trash(ids), when: some },
    ],
    { scope: "photos" },
  );

  const empty = months?.length === 0;
  return (
    <main className="wide photos-page">
      <PageTitle
        actions={
          <Link to="/albums" className="btn">
            {t("photos.albums")}
          </Link>
        }
      >
        {t("photos.title")}
      </PageTitle>
      {error && <p role="alert">{error}</p>}
      <div ref={wrap} className="photos-wrap">
        {months === null && !error && <Skeleton />}
        {empty && (
          <EmptyState icon="photo" title={t("photos.empty.title")}>
            <p>{t("photos.empty.text")}</p>
          </EmptyState>
        )}
        {months && months.length > 0 && (
          <PhotoGrid
            layout={layout}
            rowHeight={rowHeight}
            headerHeight={HEADER_H}
            section={section}
            sectionLabel={(s) => labels[s] ?? ""}
            selection={b.selection}
            focus={b.focus}
            setFocus={b.setFocus}
            label={t("photos.timeline")}
            onVisible={onVisible}
            onCellClick={b.onCellClick}
            onShiftMove={b.onShiftMove}
            control={control}
          />
        )}
      </div>
      {months && months.length > 1 && (
        <Scrubber layout={layout} months={monthKeys} listTop={listTop} />
      )}
      <SelectionBar count={selected.size} onClear={b.clear}>
        <Button icon="album" onClick={() => setAdding(ids)}>
          {t("photos.addToAlbum")}
        </Button>
        <Button icon="download" onClick={() => void downloadZip(ids, t("photos.title"))}>
          {t("photos.download")}
        </Button>
        <Button icon="trash" variant="danger" onClick={() => void trash(ids)}>
          {t("photos.trash")}
        </Button>
      </SelectionBar>
      {adding && (
        <AddToAlbumDialog nodeIds={adding} onClose={() => setAdding(null)} />
      )}
      {b.viewer}
    </main>
  );
}
