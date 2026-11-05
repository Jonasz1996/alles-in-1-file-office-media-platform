// De fototijdlijn (/fotos): al je eigen foto's en video's op opnamedatum, de nieuwste eerst, per
// maand. De maanden en hun aantallen komen eerst (/photos/months), zodat de volle hoogte en de
// scrubber meteen kloppen; de foto's van een maand pas als hij in beeld komt.
//
// Fase 6 (Z6): de filters (personen, soort, periode; Keuze 43) in een eigen chunk, "Vandaag, N jaar
// geleden" (Keuze 41) zonder filters, en de knop "Zoeken op beeld" (Keuze 81). De filters blijven
// in de state van de pagina, nooit in de adresbalk.

import { type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { crossApi } from "../crossApi";
import { useReloadOn } from "../events";
import { errorText, lazyWithAreas, t, useAreas } from "../i18n";
import { Link } from "../links";
import { markReady } from "../perf";
import { type RouteProps, useRestoreScroll } from "../router";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { useScope, useShortcuts } from "../ui/keys";
import { LazySlot } from "../ui/LazySlot";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { toast } from "../ui/toast";
import { useColumns } from "../ui/virtual";
import AddToAlbumDialog from "./AddToAlbum";
import { type MonthCount, monthLabel } from "./api";
import { downloadZip, SelectionBar, usePhotoBrowser, useStoreVersion } from "./browse";
import { GAP, type GridControl, MIN_CELL, PhotoGrid } from "./Grid";
import { reorders, useThumbPatches } from "./live";
import type { FilterBarProps } from "./people/FilterBar";
import { filteredApi, hasFilters, NO_FILTERS, type PhotoFilters, sameFilters } from "./people/filters";
import { PEOPLE_CHANGED } from "./people/types";
import type { Placed } from "./range";
import { Scrubber } from "./Scrubber";
import { SectionStore } from "./store";
import { buildLayout } from "./timeline";
import "./photos.css";

const HEADER_H = 48;
/** Veel wijzigingen vlak na elkaar (een import) geven hooguit zo vaak de maanden opnieuw. */
const RELOAD_MS = 1000;

const FilterBar = lazyWithAreas<FilterBarProps>(() => import("./people/FilterBar"), ["photos_people"]);
const Memories = lazyWithAreas(() => import("./Memories"), ["photos_people"]);

export default function PhotosPage(_: RouteProps): JSX.Element {
  useScope("photos");
  const [months, setMonths] = useState<MonthCount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState<string[] | null>(null);
  const [filters, setFilters] = useState<PhotoFilters>(NO_FILTERS);
  const filtersRef = useRef<PhotoFilters>(NO_FILTERS);
  /** De filters waarvoor de tijdlijn opnieuw begint (niets van de vorige maanden houden) */
  const fresh = useRef<PhotoFilters | null>(null);
  // De teksten van Z6 (de knop Zoeken op beeld, de lege staat met filters); FilterBar laadt ze ook
  const peopleArea = useAreas(["photos_people"]);
  const monthsRef = useRef<MonthCount[]>([]);
  const wrap = useRef<HTMLDivElement>(null);
  const control = useRef<GridControl | null>(null);
  const { columns, cellWidth } = useColumns(wrap, MIN_CELL, GAP);
  const rowHeight = Math.max(MIN_CELL / 2, Math.round(cellWidth)) + GAP;

  const store = useMemo(
    () =>
      new SectionStore((s, after) => {
        const m = monthsRef.current[s];
        return filteredApi.page(filtersRef.current, m.month, after);
      }),
    [],
  );
  const version = useStoreVersion(store);

  const loadMonths = useCallback(async () => {
    const f = filtersRef.current;
    try {
      const got = (await filteredApi.months(f)).months;
      if (f !== filtersRef.current) return; // intussen andere filters: dat antwoord komt nog
      const restart = fresh.current === f;
      if (restart) fresh.current = null;
      const before = new Map(restart ? [] : monthsRef.current.map((m, s) => [m.month, s]));
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

  // Live: een upload, iets weg of terug, of een foto die door zijn opnamedatum verschoof: de
  // maanden opnieuw (en wat in beeld is). Een thumbnail die klaar is, komt er zonder dat in.
  useReloadOn(
    (e) =>
      e.type === "connected" ||
      e.type.startsWith("node.") ||
      reorders(e) ||
      (e.type === PEOPLE_CHANGED && filtersRef.current.person.length > 0),
    () => void loadMonths(),
    RELOAD_MS,
  );
  useThumbPatches(store);

  const counts = useMemo(() => (months ?? []).map((m) => m.count), [months]);
  const layout = useMemo(() => buildLayout(counts, columns, rowHeight, HEADER_H), [counts, columns, rowHeight]);
  const labels = useMemo(() => (months ?? []).map((m) => monthLabel(m.month)), [months]);
  // Een nieuwe functie bij elke nieuwe versie van de store; de rijen van een maand tekenen
  // alleen opnieuw als juist die maand veranderde (een nieuwe array)
  const section = useMemo(() => (version >= 0 ? (s: number) => store.section(s) : () => undefined), [store, version]);
  const monthKeys = useMemo(() => (months ?? []).map((m) => m.month), [months]);
  const listTop = useCallback(() => control.current?.listTop() ?? 0, []);
  const onVisible = useCallback((sections: number[]) => store.want(sections), [store]);
  const fetchIds = useCallback((a: Placed, z: Placed) => filteredApi.ids(filtersRef.current, a, z), []);

  const b = usePhotoBrowser({ store, layout, fetchIds, control, scope: "photos" });
  const selected = b.selection.ids;
  const ids = useMemo(() => [...selected], [selected]);
  const { clear } = b;

  /** Andere filters: een nieuwe tijdlijn (niets van de oude blijft, ook de selectie niet). */
  const changeFilters = useCallback(
    (f: PhotoFilters) => {
      if (sameFilters(f, filtersRef.current)) return;
      filtersRef.current = f;
      fresh.current = f;
      setFilters(f);
      clear();
      void loadMonths();
    },
    [loadMonths, clear],
  );

  useRestoreScroll(months !== null);
  useEffect(() => {
    if (months && (months.length === 0 || store.isLoaded(0))) markReady("photos");
  }, [months, store, version]);

  async function trash(list: string[]) {
    if (list.length === 0) return;
    let done: string[];
    try {
      const r = await crossApi.trash(list);
      done = r.results.filter((x) => x.ok).map((x) => x.id);
      const failed = r.results.find((x) => !x.ok);
      if (failed) toast(errorText(failed), { kind: "error" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
      return;
    }
    b.clear();
    void loadMonths();
    if (done.length)
      toast(t("photos.trashed", { n: done.length }), {
        kind: "ok",
        action: {
          label: t("photos.undo"),
          run: () => void crossApi.restore(done).then(loadMonths, (e: unknown) => toast(errorText(e))),
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
  const filtered = hasFilters(filters);
  return (
    <main className="wide photos-page">
      <PageTitle
        actions={
          <>
            {peopleArea && (
              <Link to="/zoeken?mode=image" className="btn">
                {t("photos_people.filters.beeld")}
              </Link>
            )}
            <Link to="/albums" className="btn">
              {t("photos.albums")}
            </Link>
          </>
        }
      >
        {t("photos.title")}
      </PageTitle>
      {(filtered || (months && months.length > 0)) && (
        <LazySlot>
          <FilterBar filters={filters} onChange={changeFilters} />
        </LazySlot>
      )}
      {!filtered && months && months.length > 0 && (
        <LazySlot>
          <Memories />
        </LazySlot>
      )}
      {error && <p role="alert">{error}</p>}
      <div ref={wrap} className="photos-wrap">
        {months === null && !error && <Skeleton />}
        {empty && !filtered && (
          <EmptyState icon="photo" title={t("photos.empty.title")}>
            <p>{t("photos.empty.text")}</p>
          </EmptyState>
        )}
        {empty && filtered && peopleArea && (
          <EmptyState
            icon="filter"
            title={t("photos_people.filters.empty")}
            action={<Button onClick={() => changeFilters(NO_FILTERS)}>{t("photos_people.filters.clear")}</Button>}
          />
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
      <SelectionBar count={selected.size} onClear={b.clear} onGone={b.focusGrid}>
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
