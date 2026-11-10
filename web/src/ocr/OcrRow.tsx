// De rij "Tekst in beeld" in het infopaneel (fase 6, X1; Keuze 23 en 25a): hoe ver tekst herkennen
// is voor dit bestand (GET /files/{id}/analysis) en de knop "Tekst herkennen" (POST
// /files/{id}/ocr). Een foto: alleen de eigenaar (de tekst hoort bij zijn node, alleen hij vindt
// hem); een PDF: wie mag schrijven (de tekst hoort bij de inhoud, wie mag lezen vindt hem). Wie
// niet mag vragen, ziet de rij niet. Of je de eigenaar bent of mag schrijven, zegt GET /files/{id}
// (permission), zoals bij Personen. Een eigen chunk met het gebied ocr; InfoPanel laadt hem lazy.
//
// Na het vragen wacht de knop (aria-disabled) tot search.ready voor dit bestand komt; dan leest
// de rij de stand opnieuw. Een fout (ocr_off, ocr_running, ocr_limit, ocr_has_text) staat onder de
// knop, met de tekst uit de API of de catalogus (i18n/errors/photos.ts).
//
// Toetsenbord: Tab naar "Tekst herkennen", Enter of Spatie vraagt het. De knop blijft staan, zodat
// de focus niet verdwijnt; wat er gebeurde, komt in de role="status" erboven.

import { type JSX, useCallback, useEffect, useState } from "react";
import { api, type FileNode } from "../api";
import { useServerEvents } from "../events";
import { errorText, t } from "../i18n";
import { analysis, requestOcr } from "../search/api";
import { type Analysis, SEARCH_READY } from "../search/types";
import { seg } from "../search/wire";
import { permissionOf } from "../sharing";
import { Button } from "../ui/Button";
import { announce } from "../ui/toast";
import type { InfoRowProps } from "../viewer/infoRows";
import { mayAsk, ocrView } from "./ocr";
import "./ocr.css";

/** De stand, en of je mag vragen. */
type Loaded = { may: boolean; state: Analysis };

export default function OcrRow({ item, kind }: InfoRowProps): JSX.Element | null {
  const fileId = item.id;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [node, state] = await Promise.all([
        api.request<FileNode>("GET", `/api/v1/files/${seg(fileId)}`),
        analysis(fileId),
      ]);
      setLoaded({ may: mayAsk(kind, permissionOf(node)), state });
    } catch {
      // Weg, in de prullenbak of geen verbinding: dan geen rij (het paneel blijft werken)
      setLoaded(null);
    }
  }, [fileId, kind]);

  useEffect(() => {
    void load();
  }, [load]);

  // Tekst herkend (of de tekst van dit bestand veranderde): de stand opnieuw
  useServerEvents((event) => {
    if (event.type !== SEARCH_READY) return;
    const nodes = event.data.nodes;
    if (Array.isArray(nodes) && nodes.includes(fileId)) void load();
  });

  if (!loaded?.may) return null;
  const view = ocrView(kind, loaded.state);

  async function ask() {
    setAsking(true);
    setError(null);
    try {
      await requestOcr(fileId);
      setLoaded((l) => l && { ...l, state: { ...l.state, ocr: "queued", code: null } });
      announce(t("ocr.row.asked"));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setAsking(false);
    }
  }

  const headingId = `ocr-${fileId}`;
  const errorId = `ocr-fout-${fileId}`;
  return (
    <section className="ocr-row" aria-labelledby={headingId}>
      <h4 id={headingId}>{t("ocr.row.title")}</h4>
      <p className="muted" role="status">
        {t(view.text)}
      </p>
      {view.button && (
        <Button
          busy={asking}
          aria-disabled={view.waiting ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onClick={view.waiting ? undefined : () => void ask()}
        >
          {t(view.button === "again" ? "ocr.row.again" : "ocr.row.run")}
        </Button>
      )}
      {error && (
        <p id={errorId} className="field-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
