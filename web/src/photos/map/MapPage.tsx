// /fotos/kaart (fase 6, van Z5): je eigen foto's op een kaart zonder tegels van buiten, met de
// lijst "Plaatsen" en de bronvermelding (Keuze 31–33). Het contract: de default export is de
// pagina van de route (RouteProps), in een eigen chunk (mapRoute ≤ 30 kB).
//
// Wat de kaart vraagt (Keuze 32): de cellen van GET /photos/map voor het venster met een kwart
// extra, op hele cellen, pas als het venster even stilstaat; een nieuw verzoek breekt het vorige
// af. Vanaf zoom 6 de plaatsnaam van de grootste groepen (GET /files/{omslag}/location), en de
// thumbnails van de omslagen (POST /previews). Alles bij de app zelf, niets naar andere hosts.
// Waar je kijkt, staat nooit in het adres (het access-log, Keuze 27): alleen ?bestand= van de
// viewer, als je de foto's van een groep opent.

import { type JSX, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../../api";
import { errorText, t } from "../../i18n";
import type { RouteProps } from "../../router";
import type { Filters } from "../../search/types";
import { Button, IconButton } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { PageTitle } from "../../ui/PageTitle";
import { toast } from "../../ui/toast";
import { useViewerParam, Viewer } from "../../viewer";
import type { PhotoItem } from "../api";
import { geoAttribution, places as fetchPlaces } from "./api";
import { coverRect, type Dot, type Loaded, needsCells, placeDots, requestBBox } from "./cells";
import { clearMapFocus, onMapFocus, peekMapFocus } from "./focus";
import { homeRect } from "./home";
import { isAbort, LatestOnly, SETTLE_MS } from "./loader";
import { Lookup, labelCovers, PLACE_ZOOM, placeOf } from "./lookup";
import { MapView } from "./MapView";
import { PlacesList } from "./PlacesList";
import { latToY, lonToX } from "./projection";
import { cellsPath, getCells, getItems, getLocation, itemsPath } from "./requests";
import { appLocale, attributionLines, dotLabel, sortPlaces } from "./text";
import { type GeoAttribution, MAX_CELLS, MAX_ZOOM, type MapCell, type MapCells, type Place, type PlaceName } from "./types";
import { cellZoom, clampView, fitRect, MAX_VIEW_ZOOM, minZoom, type Rect, type Size, type View, zoomAround } from "./view";
import { type Country, countryAt, countryName, loadWorld } from "./world";
import "./map.css";

/** "Op de kaart" vanuit de rij Plek: zo ver ingezoomd dat er nog een kust of grens in beeld is (de
 * kaart heeft alleen landsgrenzen, geen straten); de ring wijst de plek zelf aan. */
const FOCUS_ZOOM = 9;
/** Hooguit zoveel thumbnails per POST /previews. */
const PREVIEW_BATCH = 500;

type Home = { state: "loading" } | { state: "empty" } | { state: "ok"; rect: Rect } | { state: "error" };

interface Cells {
  cells: MapCell[];
  z: number;
  /** De server gaf er MAX_CELLS: niet alles in dit stuk staat erop */
  truncated: boolean;
}

/** Een verzoek om cellen, met wat het antwoord betekent. */
interface Ask extends Loaded {
  path: string;
  query: Filters;
  /** Met een kwart extra om het venster heen */
  padded: boolean;
}

/** De foto's van één groep in de viewer. */
interface Photos {
  items: PhotoItem[];
  cursor: string | null;
  rect: Rect;
  query: Filters;
}

/** Het venster van Home: al je foto's, of de hele wereld als er (nog) niets is. */
function homeView(rect: Rect | null, size: Size): View {
  return rect ? fitRect(rect, size) : clampView({ x: 0.5, y: 0.5, zoom: 0 }, size);
}

export default function MapPage(_: RouteProps): JSX.Element {
  // Kwam je hier met "Op de kaart"? Alleen de eerste keer; nooit in het adres
  const [focus, setFocus] = useState(peekMapFocus);
  const mark = useMemo(() => (focus ? ([lonToX(focus.lon), latToY(focus.lat)] as const) : null), [focus]);

  const [countries, setCountries] = useState<Country[] | null>(null);
  const [noWorld, setNoWorld] = useState(false);
  const [size, setSize] = useState<Size | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [place, setPlace] = useState<number | null>(null);
  const [home, setHome] = useState<Home>({ state: "loading" });
  const [cells, setCells] = useState<Cells | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Laden er cellen? Dan staat de kaart op aria-busy, tot het antwoord er staat */
  const [loading, setLoading] = useState(false);
  const [placeList, setPlaceList] = useState<Place[] | null>(null);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [attribution, setAttribution] = useState<GeoAttribution | null>(null);
  /** Na het stilstaan: hoeveel groepen er in beeld zijn (voor een schermlezer), of dat er niets is */
  const [settled, setSettled] = useState<{ text: string; none: boolean } | null>(null);
  const [photos, setPhotos] = useState<Photos | null>(null);
  const [viewing, setViewing] = useViewerParam();
  const [, setTick] = useState(0);

  const query = useMemo<Filters>(() => (place === null ? {} : { place }), [place]);
  const queryKey = place === null ? "" : `place=${place}`;

  // --- het venster: een stap per beeld (requestAnimationFrame), Home en de eerste keer meteen ---
  const viewRef = useRef<View | null>(null);
  const sizeRef = useRef<Size | null>(null);
  const pending = useRef<View | null>(null);
  const frame = useRef(0);
  const commit = useCallback((v: View) => {
    viewRef.current = v;
    setView(v);
  }, []);
  const changeView = useCallback(
    (f: (v: View) => View) => {
      const base = pending.current ?? viewRef.current;
      const s = sizeRef.current;
      if (!base || !s) return;
      pending.current = clampView(f(base), s);
      if (frame.current) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = 0;
        const next = pending.current;
        pending.current = null;
        if (next) commit(next);
      });
    },
    [commit],
  );
  const jump = useCallback(
    (v: View) => {
      pending.current = null;
      const s = sizeRef.current;
      commit(s ? clampView(v, s) : v);
    },
    [commit],
  );
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  /** Kwam je met "Op de kaart", dan blijft die plek staan als Home daarna binnenkomt */
  const keepFocus = useRef(focus !== null);
  /** Het element van de kaart (MapView), om de focus erheen te zetten */
  const mapBox = useRef<HTMLDivElement>(null);
  /** "Op de kaart" in de viewer: de kaart krijgt de focus zodra de viewer weg is */
  const focusAfterViewer = useRef(false);
  // "Op de kaart" terwijl de kaart al open is (in de viewer van een groep): meteen erheen
  useEffect(() => {
    clearMapFocus();
    return onMapFocus((f) => {
      keepFocus.current = true;
      focusAfterViewer.current = true;
      setFocus(f);
      jump({ x: lonToX(f.lon), y: latToY(f.lat), zoom: FOCUS_ZOOM });
    });
  }, [jump]);
  const onSize = useCallback(
    (s: Size) => {
      sizeRef.current = s;
      setSize(s);
      if (viewRef.current) jump(viewRef.current);
    },
    [jump],
  );
  const sized = size !== null;

  // --- de wereld (een statisch bestand van de app), de bronnen en de plaatsen ---
  useEffect(() => {
    let live = true;
    loadWorld().then(
      (w) => {
        if (live) setCountries(w);
      },
      () => {
        if (live) setNoWorld(true);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    geoAttribution().then(
      (a) => {
        if (live) setAttribution(a);
      },
      () => {},
    );
    fetchPlaces().then(
      (r) => {
        if (live) setPlaceList(sortPlaces(r.places));
      },
      (e: unknown) => {
        if (live) setPlaceError(errorText(e));
      },
    );
    return () => {
      live = false;
    };
  }, []);

  // --- Home: het stuk met al je foto's, opnieuw bij een ander filter ---
  const homeRef = useRef<Rect | null>(null);
  /** Er kwam al eens een Home binnen (niet bij het dubbele effect van StrictMode) */
  const homeDone = useRef(false);
  // Opnieuw als de kaart zijn grootte kent, en bij een ander filter. Op sized, niet op sizeRef:
  // MapView meet in zijn eigen effect, en dat draait vóór dit effect. Met sizeRef ging Home dus al
  // in de eerste ronde (sized nog false) en meteen nog eens als sized true werd: zoom 0 twee keer
  // gevraagd, de eerste afgebroken (review golf 3, web F6)
  useEffect(() => {
    const s = sizeRef.current;
    if (!sized || !s) return;
    // Een ander filter: daarna weer naar Home, tenzij er intussen "Op de kaart" komt
    if (homeDone.current) keepFocus.current = false;
    if (keepFocus.current && focus && !viewRef.current) {
      jump({ x: lonToX(focus.lon), y: latToY(focus.lat), zoom: FOCUS_ZOOM });
    }
    const ctl = new AbortController();
    setHome({ state: "loading" });
    homeRect(getCells, query, s, ctl.signal).then(
      (rect) => {
        homeRef.current = rect;
        homeDone.current = true;
        setHome(rect ? { state: "ok", rect } : { state: "empty" });
        if (keepFocus.current) keepFocus.current = false;
        else jump(homeView(rect, sizeRef.current ?? s));
      },
      (e: unknown) => {
        if (ctl.signal.aborted || isAbort(e)) return;
        keepFocus.current = false;
        homeDone.current = true;
        setHome({ state: "error" });
        setError(errorText(e));
        if (!viewRef.current) jump(homeView(null, sizeRef.current ?? s));
      },
    );
    return () => ctl.abort();
  }, [sized, queryKey]);

  // --- de cellen: pas als het venster stilstaat, alleen het laatste verzoek telt ---
  const loaded = useRef<Loaded | null>(null);
  const asked = useRef<Ask | null>(null);
  /** Op deze zoom en met deze filters gaf de server te veel cellen: vraag dan zonder rand */
  const dense = useRef<Loaded | null>(null);
  const loader = useMemo(() => {
    const latest: LatestOnly<MapCells> = new LatestOnly<MapCells>({
      delay: SETTLE_MS,
      run: (path, signal) => getCells(path, signal),
      done: (path, res) => {
        const a = asked.current;
        if (!a || a.path !== path) return;
        const full = res.cells.length >= MAX_CELLS;
        const v = viewRef.current;
        const s = sizeRef.current;
        if (full && a.padded && v && s) {
          // Te veel met de rand erbij: dan alleen wat in beeld is
          dense.current = { rect: a.rect, z: a.z, filters: a.filters };
          const rect = coverRect(v, s, a.z, 0);
          ask({ ...a, rect, padded: false, path: cellsPath(requestBBox(rect), a.z, a.query) }, 0);
          return;
        }
        if (dense.current?.z === a.z && res.cells.length < MAX_CELLS / 2) dense.current = null;
        loaded.current = { rect: a.rect, z: a.z, filters: a.filters };
        setError(null);
        setCells({ cells: res.cells, z: a.z, truncated: full });
      },
      failed: (_path, e) => setError(errorText(e)),
      busy: setLoading,
    });
    function ask(a: Ask, delay?: number): void {
      asked.current = a;
      latest.request(a.path, delay);
    }
    return { ask, latest };
  }, []);
  useEffect(() => () => loader.latest.cancel(), [loader]);

  // Een ander filter: wat er stond, hoort er niet meer bij
  useEffect(() => {
    loaded.current = null;
    dense.current = null;
    setCells(null);
    setSettled(null);
  }, [queryKey]);

  // Cellen erbij als het venster buiten wat er geladen is komt (query hoort bij queryKey)
  useEffect(() => {
    if (!view || !size) return;
    const z = cellZoom(view);
    if (!needsCells(loaded.current, view, size, z, queryKey)) {
      loader.latest.cancel();
      return;
    }
    const tight = dense.current?.z === z && dense.current.filters === queryKey;
    const rect = coverRect(view, size, z, tight ? 0 : undefined);
    loader.ask(
      { path: cellsPath(requestBBox(rect), z, query), rect, z, filters: queryKey, query, padded: !tight },
      loaded.current ? undefined : 0,
    );
  }, [view, size, queryKey, loader]);

  // --- thumbnails van de omslagen en, vanaf zoom 6, de plaatsnamen; ook pas na het stilstaan ---
  const bump = useCallback(() => setTick((n) => n + 1), []);
  const [thumbs] = useState(
    () =>
      new Lookup<string>(
        async (ids) => {
          const got = await api.previews(ids);
          return new Map(ids.map((id) => [id, got[id]?.thumb ?? null]));
        },
        bump,
        PREVIEW_BATCH,
        1,
      ),
  );
  const [names] = useState(
    () =>
      new Lookup<PlaceName>(
        async (ids, signal) => {
          const out = new Map<string, PlaceName | null>();
          for (const id of ids) {
            try {
              out.set(id, placeOf(await getLocation(id, signal)));
            } catch (e) {
              // 404: geen plek (of niet van jou); dan geen naam. Iets anders: later opnieuw
              if (e instanceof ApiError && e.status === 404) out.set(id, null);
              else throw e;
            }
          }
          return out;
        },
        bump,
        1,
        3,
      ),
  );
  const settle = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!view || !size || !cells) return;
    const timer = setTimeout(() => {
      settle.current?.abort();
      const ctl = new AbortController();
      settle.current = ctl;
      const shown = placeDots(cells.cells, cells.z, view, size);
      void thumbs.want(
        shown.map((d) => d.cell.cover),
        ctl.signal,
      );
      void names.want(labelCovers(shown, view.zoom), ctl.signal);
      setSettled(
        shown.length === 0
          ? { text: t("photos_map.map.nothingHere"), none: true }
          : { text: t("photos_map.map.status", { n: shown.length }), none: false },
      );
    }, SETTLE_MS);
    return () => clearTimeout(timer);
  }, [cells, view, size, thumbs, names]);
  useEffect(() => () => settle.current?.abort(), []);

  // --- de stippen ---
  const dots = useMemo(
    () => (view && size && cells ? placeDots(cells.cells, cells.z, view, size) : []),
    [cells, view, size],
  );
  const labelled = useMemo(() => new Set(view ? labelCovers(dots, view.zoom) : []), [dots, view]);
  const countryCache = useRef(new Map<string, string | null>());
  function countryOf(d: Dot): string | null {
    if (!countries) return null;
    let name = countryCache.current.get(d.key);
    if (name === undefined) {
      const c = countryAt(countries, d.cell.lon, d.cell.lat);
      name = c ? countryName(c, appLocale()) : null;
      countryCache.current.set(d.key, name);
    }
    return name;
  }
  function placeName(d: Dot): string | null {
    return view && view.zoom >= PLACE_ZOOM ? (names.get(d.cell.cover)?.name ?? null) : null;
  }

  // --- een groep kiezen: inzoomen, of de foto's openen als het niet verder kan ---
  const loadingMore = useRef(false);
  async function openPhotos(rect: Rect): Promise<void> {
    try {
      const res = await getItems(itemsPath(requestBBox(rect), query));
      if (res.items.length === 0) return;
      setPhotos({ items: res.items, cursor: res.cursor, rect, query });
      setViewing(res.items[0].id);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }
  async function more(): Promise<void> {
    const p = photos;
    if (!p?.cursor || loadingMore.current) return;
    loadingMore.current = true;
    try {
      const res = await getItems(itemsPath(requestBBox(p.rect), p.query, p.cursor));
      setPhotos((cur) => (cur === p ? { ...p, items: [...p.items, ...res.items], cursor: res.cursor } : cur));
    } catch {
      // De volgende keer aan het eind opnieuw
    } finally {
      loadingMore.current = false;
    }
  }
  /** Een groep kiezen. true: de kaart zoomde in (de focus gaat dan naar de kaart). */
  function activate(d: Dot): boolean {
    const s = sizeRef.current;
    if (!s || !cells) return false;
    const rect = d.rect;
    if (d.n > 1 && cells.z < MAX_ZOOM) {
      changeView((v) => {
        const fit = fitRect(rect, s, MAX_VIEW_ZOOM, 48);
        return fit.zoom >= v.zoom + 1 ? fit : zoomAround(v, s, v.zoom + 2, d.sx, d.sy);
      });
      return true;
    }
    void openPhotos(rect);
    return false;
  }

  // Een ?bestand= van een vorige keer (herladen): de groep is weg, dus de viewer ook
  useEffect(() => {
    if (viewing && !photos) setViewing(null);
  }, []);
  const viewIndex = photos && viewing ? photos.items.findIndex((i) => i.id === viewing) : -1;
  const shown = photos !== null && viewIndex >= 0;
  // Na "Op de kaart" sluit de viewer (het adres verliest ?bestand=); de stip die hem opende is na
  // het springen meestal weg. Dan niet de body maar de kaart (review golf 3, web F3)
  useEffect(() => {
    if (shown || !focusAfterViewer.current) return;
    focusAfterViewer.current = false;
    mapBox.current?.focus({ preventScroll: true });
  }, [shown, focus]);

  function goHome(): void {
    const s = sizeRef.current;
    if (s) jump(homeView(homeRef.current, s));
  }
  function zoomBy(by: number): void {
    const s = sizeRef.current;
    if (s) changeView((v) => zoomAround(v, s, v.zoom + by, s.w / 2, s.h / 2));
  }
  function choose(id: number | null): void {
    setPhotos(null);
    setPlace(id);
  }
  /** "Alle plaatsen" boven de kaart: de knop verdwijnt met het filter, dus de kaart krijgt de
   * focus (review golf 3, web F2) */
  function chooseAll(): void {
    mapBox.current?.focus({ preventScroll: true });
    choose(null);
  }
  const atMax = !view || view.zoom >= MAX_VIEW_ZOOM;
  const atMin = !view || !size || view.zoom <= minZoom(size) + 1e-6;

  const chosen = place === null ? null : (placeList?.find((p) => p.id === place) ?? null);
  const empty = home.state === "empty" && place === null && !focus;
  return (
    <main className="wide map-page">
      <PageTitle>{t("photos_map.title")}</PageTitle>
      {empty ? (
        <EmptyState icon="photo" title={t("photos_map.map.empty")}>
          {t("photos_map.map.emptyHint")}
        </EmptyState>
      ) : (
        <div className="map-layout">
          <div className="map-main">
            {chosen && (
              <p className="map-only">
                {t("photos_map.places.only", { place: chosen.name })}{" "}
                <Button variant="link" onClick={chooseAll}>
                  {t("photos_map.places.all")}
                </Button>
              </p>
            )}
            <MapView
              countries={countries}
              view={view}
              dots={dots}
              labelOf={(d) => dotLabel(d.n, placeName(d) ?? countryOf(d))}
              nameOf={(d) => (labelled.has(d.cell.cover) ? placeName(d) : null)}
              thumbOf={(d) => thumbs.get(d.cell.cover)}
              mark={mark}
              busy={loading}
              describedBy="kaart-hulp"
              onSize={onSize}
              onView={changeView}
              onHome={goHome}
              onActivate={activate}
              boxRef={mapBox}
            >
              {/* Op de grens aria-disabled en niets doen, niet disabled: dan zou de focus van
                  de knop naar de body vallen (review golf 3, web F4) */}
              <div className="map-controls">
                <IconButton
                  icon="zoom-in"
                  label={t("photos_map.map.zoomIn")}
                  aria-disabled={atMax || undefined}
                  onClick={() => {
                    if (!atMax) zoomBy(1);
                  }}
                />
                <IconButton
                  icon="zoom-out"
                  label={t("photos_map.map.zoomOut")}
                  aria-disabled={atMin || undefined}
                  onClick={() => {
                    if (!atMin) zoomBy(-1);
                  }}
                />
                <IconButton icon="fullscreen" label={t("photos_map.map.home")} disabled={!view} onClick={goHome} />
              </div>
              <div className="map-notices">
                {!view && <p>{t("photos_map.map.loading")}</p>}
                {error && <p role="alert">{error}</p>}
                {noWorld && <p>{t("photos_map.map.noWorld")}</p>}
                {cells?.truncated && <p>{t("photos_map.map.tooMany")}</p>}
                {settled?.none && home.state === "ok" && <p>{settled.text}</p>}
              </div>
            </MapView>
            <p id="kaart-hulp" className="muted map-help">
              {t("photos_map.map.help")}
            </p>
            <p className="sr-only" role="status">
              {settled?.text ?? ""}
            </p>
          </div>
          <aside className="map-side">
            <PlacesList places={placeList} error={placeError} chosen={place} onChoose={choose} />
            <footer className="map-attribution muted">
              {attributionLines(attribution).map((line) => (
                <p key={line}>{line}</p>
              ))}
              <p>{t("photos_map.attribution.borders")}</p>
            </footer>
          </aside>
        </div>
      )}
      {photos && viewIndex >= 0 && (
        <Suspense fallback={null}>
          <Viewer
            items={photos.items}
            index={viewIndex}
            onIndexChange={(i) => setViewing(photos.items[i].id)}
            onClose={() => setViewing(null)}
            onNeedMore={(direction) => {
              if (direction === 1) void more();
            }}
          />
        </Suspense>
      )}
    </main>
  );
}
