// De fototijdlijn (/fotos): al je eigen foto's en video's op opnamedatum, de nieuwste eerst, per
// maand. De maanden en hun aantallen komen eerst (/photos/months), zodat de volle hoogte en de
// scrubber meteen kloppen; de foto's van een maand pas als hij in beeld komt.
//
// Fase 6 (Z6): de filters (personen, soort, periode; Keuze 43) in een eigen chunk, "Vandaag, N jaar
// geleden" (Keuze 41) zonder filters, en de knop "Zoeken op beeld" (Keuze 81). De filters blijven
// in de state van de pagina, nooit in de adresbalk. Filters en herinneringen komen tegelijk met de
// maanden en verschijnen in dezelfde commit als de tijdlijn (loadTop): niets schuift omlaag.

import { type ComponentType, type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { crossApi } from "../crossApi";
import { useReloadOn } from "../events";
import { errorText, loadAreas, t, useAreas } from "../i18n";
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
import type { MemoriesProps } from "./Memories";
import { memories } from "./people/api";
import type { FilterBarProps } from "./people/FilterBar";
import { filteredApi, hasFilters, NO_FILTERS, type PhotoFilters, sameFilters } from "./people/filters";
import { type MemoryYear, PEOPLE_CHANGED } from "./people/types";
import type { Placed } from "./range";
import { Scrubber } from "./Scrubber";
import { SectionStore } from "./store";
import { buildLayout } from "./timeline";
import "./photos.css";

const HEADER_H = 48;
/** Veel wijzigingen vlak na elkaar (een import) geven hooguit zo vaak de maanden opnieuw. */
const RELOAD_MS = 1000;

/** Zo lang wacht de tijdlijn hooguit op wat erboven komt; daarna eerst de tijdlijn. */
const TOP_WAIT_MS = 1500;

/** Wat boven de tijdlijn staat (fase 6, van Z6): de balk Filters en de herinneringen, elk een
 * eigen chunk met het gebied photos_people, en het antwoord van GET /photos/memories. */
interface Top {
  Bar: ComponentType<FilterBarProps> | null;
  Memories: ComponentType<MemoriesProps> | null;
  years: MemoryYear[] | null;
}

/** Begint tegelijk met de maanden. Lukt een chunk niet, dan staat dat deel er niet (de tijdlijn
 * werkt gewoon); lukt het antwoord niet, dan geen herinneringen. */
function loadTop(): Promise<Top> {
  const areas = loadAreas(["photos_people"]);
  return Promise.all([
    Promise.all([import("./people/FilterBar"), areas]).then(
      ([m]) => m.default,
      () => null,
    ),
    Promise.all([import("./Memories"), areas]).then(
      ([m]) => m.default,
      () => null,
    ),
    memories().then(
      (r) => r.years,
      () => [],
    ),
  ]).then(([Bar, Memories, years]) => ({ Bar, Memories, years }));
}

export default function PhotosPage(_: RouteProps): JSX.Element {
  useScope("photos");
  const [months, setMonths] = useState<MonthCount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState<string[] | null>(null);
  const [filters, setFilters] = useState<PhotoFilters>(NO_FILTERS);
  const filtersRef = useRef<PhotoFilters>(NO_FILTERS);
  /** De filters waar de getoonde maanden bij horen. Tussen een wijziging en het nieuwe antwoord
   * zijn dat nog de oude: wis je de filters terwijl er niets paste, dan blijft de balk staan (met
   * de focus erin) tot de hele tijdlijn er is. */
  const [monthsFor, setMonthsFor] = useState<PhotoFilters>(NO_FILTERS);
  const root = useRef<HTMLElement>(null);
  /** De filters waarvoor de tijdlijn opnieuw begint (niets van de vorige maanden houden) */
  const fresh = useRef<PhotoFilters | null>(null);
  // De teksten van Z6 (de knop Zoeken op beeld, de lege staat met filters); FilterBar laadt ze ook
  const peopleArea = useAreas(["photos_people"]);
  const monthsRef = useRef<MonthCount[]>([]);
  /** De filters en de herinneringen; de tijdlijn verschijnt in dezelfde commit (topWait), anders
   * schuift hij omlaag als ze later binnenkomen (CLS, review golf 2, web F) */
  const [top, setTop] = useState<Top | null>(null);
  const topWait = useRef<Promise<unknown> | null>(null);
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
      await topWait.current; // alleen de eerste keer echt wachten
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
      setMonthsFor(f);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [store]);

  useEffect(() => {
    let live = true;
    const loaded = loadTop().then((t) => {
      if (live) setTop(t);
    });
    topWait.current = Promise.race([loaded, new Promise((ok) => setTimeout(ok, TOP_WAIT_MS))]);
    return () => {
      live = false;
    };
  }, []);

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

  /** Filters wissen vanuit de lege staat: die verdwijnt, dus de focus naar het veld Persoon. */
  function clearFromEmpty() {
    changeFilters(NO_FILTERS);
    requestAnimationFrame(() => root.current?.querySelector<HTMLElement>(".photo-filters [role=combobox]")?.focus());
  }

  const empty = months?.length === 0;
  const filtered = hasFilters(filters);
  const Bar = top?.Bar;
  const MemoriesView = top?.Memories;
  const shownFiltered = hasFilters(monthsFor);
  return (
    <main ref={root} className="wide photos-page">
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
      {/* Het wachten staat boven wat erna komt en gaat dan weg: wat op zijn plek komt, is nieuw en
          verschuift niets (een raster dat al stond en omlaag ging, telde wel als layout shift) */}
      {months === null && !error && <Skeleton />}
      {Bar && (filtered || shownFiltered || (months && months.length > 0)) && (
        <LazySlot>
          <Bar filters={filters} onChange={changeFilters} />
        </LazySlot>
      )}
      {MemoriesView && months && months.length > 0 && (
        <LazySlot>
          <MemoriesView initial={top?.years ?? null} hidden={filtered} />
        </LazySlot>
      )}
      {error && <p role="alert">{error}</p>}
      <div ref={wrap} className={months === null ? "photos-wrap photos-wrap-wait" : "photos-wrap"}>
        {empty && !shownFiltered && (
          <EmptyState icon="photo" title={t("photos.empty.title")}>
            <p>{t("photos.empty.text")}</p>
          </EmptyState>
        )}
        {empty && shownFiltered && peopleArea && (
          <EmptyState
            icon="filter"
            title={t("photos_people.filters.empty")}
            action={<Button onClick={clearFromEmpty}>{t("photos_people.filters.clear")}</Button>}
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
