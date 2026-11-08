// De tekstlaag over een pagina van de PDF-viewer (fase 6, Z4; Keuze 18): de woorden van de pagina
// doorzichtig op hun plek, zodat selecteren en kopiëren werkt, en de woorden van de zoekvraag
// (?q=) gemarkeerd. In de lazy chunk van de PDF-viewer.
//
// Elk woord is een eigen element op precies zijn vak (x, y, w, h als deel van de pagina). De
// letters krijgen de hoogte van het vak; zodat ze ook even breed zijn als het vak, meet een canvas
// het woord één keer en rekt scaleX het bij (zoals pdf.js). Zonder canvas (vitest) blijft het vak
// goed en volgen de letters niet precies: de markering zit op het vak, niet op de letters. Na elk
// woord een spatie, en na een regel een <br>, zodat kopiëren gewone tekst geeft.
//
// useTextLayer: de woorden per pagina (na het laden van de afbeelding van die pagina, dan staan
// ze al in de cache van de server), en met ?q= de eerste pagina met een treffer om naartoe te
// gaan, met wat er gevonden is in een role="status".

import { Fragment, type JSX, memo, type ReactNode, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useServerEvents } from "../events";
import { t, useAreas } from "../i18n";
import { useLocation } from "../router";
import { pageWords } from "../search/api";
import { SEARCH_READY, type PageWord } from "../search/types";
import {
  firstHit,
  type FirstHit,
  markQuery,
  matcher,
  PageWordsStore,
  place,
  queryTerms,
  type ScanResult,
} from "./textLayer";
import type { ViewerItem } from "./types";
import "./textLayer.css";

/** De lettergrootte waarop het canvas meet (px); de verhouding telt. */
const MEASURE_PX = 100;
const FONT = "sans-serif";

let measureContext: OffscreenCanvasRenderingContext2D | null | undefined;

/** De breedte van een woord in em, of 0 als er niet te meten is. */
function naturalWidth(text: string): number {
  if (measureContext === undefined) {
    measureContext = typeof OffscreenCanvas === "undefined" ? null : new OffscreenCanvas(1, 1).getContext("2d");
    if (measureContext) measureContext.font = `${MEASURE_PX}px ${FONT}`;
  }
  return measureContext ? measureContext.measureText(text).width / MEASURE_PX : 0;
}

const pct = (v: number) => `${(v * 100).toFixed(3)}%`;

export interface TextLayerProps {
  words: readonly PageWord[];
  match: ((text: string) => boolean) | null;
  /** De pagina in pixels zoals ze nu getekend is */
  width: number;
  height: number;
}

function TextLayerView({ words, match, width, height }: TextLayerProps): JSX.Element {
  const placed = useMemo(() => place(words, match), [words, match]);
  // Eén keer meten per woord; bij zoomen verandert alleen de lettergrootte van de laag
  const natural = useMemo(() => words.map((w) => naturalWidth(w.t)), [words]);
  const aspect = height > 0 ? width / height : 1;
  return (
    <div className="text-layer" style={{ fontSize: `${height}px` }}>
      {placed.map(({ word, marked, lineEnd }, i) => {
        // De letters zijn h hoog (em van de laag); scaleX maakt ze w breed
        const scale = natural[i] > 0 && word.h > 0 ? (word.w * aspect) / (natural[i] * word.h) : 0;
        const style = {
          left: pct(word.x),
          top: pct(word.y),
          width: pct(scale > 0 ? word.w / scale : word.w),
          height: pct(word.h),
          fontSize: `${word.h}em`,
          transform: scale > 0 ? `scaleX(${scale.toFixed(4)})` : undefined,
        };
        const text = `${word.t}${lineEnd ? "" : " "}`;
        return (
          <Fragment key={i}>
            {marked ? <mark style={style}>{text}</mark> : <span style={style}>{text}</span>}
            {lineEnd && i < placed.length - 1 && <br />}
          </Fragment>
        );
      })}
    </div>
  );
}

/** Alleen opnieuw tekenen als de woorden, de vraag of de grootte veranderen (niet bij scrollen). */
export const TextLayer = memo(TextLayerView);

/** Wat PageViewer van de tekstlaag krijgt. */
export interface TextLayerHooks {
  overlay(n: number, width: number, height: number): ReactNode;
  onPageLoad(n: number): void;
  jumpTo: FirstHit | null;
  status: ReactNode;
}

function statusText(result: ScanResult | null): string {
  if (!result) return "";
  switch (result.kind) {
    case "found":
      return t("search.viewer.found", { n: result.page });
    case "limit":
      return t("search.viewer.notInFirst", { n: result.pages });
    case "no_text":
      return t("search.viewer.noText");
    default:
      return t("search.viewer.notFound");
  }
}

/** De tekstlaag voor een PDF met count pagina's. token: het download-token van de viewer; null
 * via een publieke link (daar zijn geen woorden: 404) of zolang /document er niet is. */
export function useTextLayer(item: ViewerItem, token: string | null, count: number): TextLayerHooks | null {
  // Een andere versie of inhoud: andere woorden
  const store = useMemo(() => new PageWordsStore(), [item.id, item.version?.id, item.etag]);
  useEffect(() => {
    store.setLoader(token ? (n) => pageWords(item.id, n, token).then((r) => r.words) : null);
  }, [store, item.id, token]);
  useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot);

  const { query } = useLocation();
  const q = token ? markQuery(query) : null;
  const terms = useMemo(() => queryTerms(q), [q]);
  const match = useMemo(() => (terms ? matcher(terms) : null), [terms]);
  // De teksten van de melding staan in het gebied search (via /zoeken is het er al)
  const texts = useAreas(match ? ["search"] : []);

  // Zoeken naar de eerste treffer begint na de eerste pagina die laadt: die render zette de
  // woorden van de eerste pagina's al in de cache van de server
  const [loaded, setLoaded] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  useEffect(() => {
    setResult(null);
    if (!match || !loaded) return;
    let live = true;
    firstHit(count, (n) => store.want(n), match, () => live).then(
      (r) => live && setResult(r),
      () => {},
    );
    return () => {
      live = false;
    };
  }, [match, loaded, count, store]);

  // OCR (X1) maakte tekst bij dit bestand: pagina's zonder woorden opnieuw vragen
  useServerEvents((event) => {
    if (event.type !== SEARCH_READY) return;
    const nodes = event.data.nodes;
    if (Array.isArray(nodes) && nodes.includes(item.id)) store.forgetEmpty();
  });

  const onPageLoad = useCallback(
    (n: number) => {
      setLoaded(true);
      void store.want(n);
    },
    [store],
  );

  if (!token) return null;
  return {
    overlay: (n, width, height) => {
      const words = store.get(n);
      return words && words.length > 0 ? <TextLayer words={words} match={match} width={width} height={height} /> : null;
    },
    onPageLoad,
    jumpTo: result?.kind === "found" ? result : null,
    status: match ? (
      <span className="pages-found" role="status">
        {texts ? statusText(result) : ""}
      </span>
    ) : null,
  };
}
