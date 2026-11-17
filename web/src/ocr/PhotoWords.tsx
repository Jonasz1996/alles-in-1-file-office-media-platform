// De woorden van de zoekvraag in een foto (fase 6, X1; Keuze 25a): kom je van /zoeken (?q= naast
// ?bestand=), dan legt de viewer de tekst die OCR in de foto vond als laag over het beeld, met de
// woorden van de vraag gemarkeerd: een kader op hun plek. Dezelfde laag als in de PDF-viewer
// (viewer/TextLayer.tsx, van Z4), op dezelfde maat en met dezelfde transform als de img in
// ZoomView, zodat hij meezoomt, -schuift en -draait.
//
// De woorden komen van GET /files/{id}/pages/1/words?token= met het download-token van de viewer
// (POST /download-link). Alleen de eigenaar krijgt ze (de tekst hoort bij zijn node); een ander,
// ook via een deling, krijgt 404 en dan is er geen laag. Een publieke link en een eerdere versie
// hebben geen woorden: ZoomView laadt dit dan niet. Een eigen chunk met het gebied ocr.
//
// Hoeveel woorden er gemarkeerd zijn, staat in een role="status" voor een schermlezer; de laag
// zelf is aria-hidden (de tekst staat al in de zoekresultaten).

import { type CSSProperties, type JSX, useEffect, useMemo, useState } from "react";
import { useServerEvents } from "../events";
import { t } from "../i18n";
import { useLocation } from "../router";
import { pageWords } from "../search/api";
import { type PageWord, SEARCH_READY } from "../search/types";
import { defaultSource } from "../viewer/source";
import { TextLayer } from "../viewer/TextLayer";
import { markQuery, matcher, queryTerms, tokenOf } from "../viewer/textLayer";
import type { ViewerItem } from "../viewer/types";
import "./ocr.css";

export interface PhotoWordsProps {
  item: ViewerItem;
  /** De maat van de foto zoals ZoomView hem tekent (vóór de transform) */
  width: number;
  height: number;
  /** Dezelfde maat en transform als de img */
  style: CSSProperties;
}

export default function PhotoWords({ item, width, height, style }: PhotoWordsProps): JSX.Element | null {
  const { query } = useLocation();
  const q = markQuery(query);
  const match = useMemo(() => {
    const terms = queryTerms(q);
    return terms ? matcher(terms) : null;
  }, [q]);
  const [words, setWords] = useState<readonly PageWord[] | null>(null);
  // Een nieuwe ronde na search.ready voor deze foto (de tekst kwam net binnen)
  const [round, setRound] = useState(0);
  const wanted = match !== null;

  useEffect(() => {
    if (!wanted) return;
    let live = true;
    (async () => {
      try {
        const token = tokenOf(await defaultSource().url(item, false));
        if (!token) return;
        const got = await pageWords(item.id, 1, token);
        if (live) setWords(got.words);
      } catch {
        // 404 (geen tekst, of niet jouw foto) of geen verbinding: geen laag
        if (live) setWords([]);
      }
    })();
    return () => {
      live = false;
    };
  }, [item.id, item.etag, wanted, round]);

  useServerEvents((event) => {
    if (event.type !== SEARCH_READY) return;
    const nodes = event.data.nodes;
    if (Array.isArray(nodes) && nodes.includes(item.id)) setRound((n) => n + 1);
  });

  if (!match || !words || words.length === 0) return null;
  const hits = words.filter((w) => match(w.t)).length;
  return (
    <>
      <div className="photo-words" style={style} aria-hidden="true">
        <TextLayer words={words} match={match} width={width} height={height} />
      </div>
      <p className="sr-only" role="status">
        {hits > 0 ? t("ocr.photo.found", { n: hits }) : t("ocr.photo.notFound")}
      </p>
    </>
  );
}
