// De bronvermelding van TMDB (fase 5, M8, Keuze 40): "Gegevens: TMDB" met een link naar TMDB, en
// de zin die TMDB eist (in het Engels, zoals TMDB hem geeft). In het venster Koppelen en bij de
// gegevens van een item dat van TMDB komt. Op /beheer staat hij bij M10.

import type { JSX } from "react";
import { t } from "../../i18n";
import { TMDB_URL } from "./match";
import "./match.css";

export function TmdbAttribution(): JSX.Element {
  return (
    <p className="media-attribution muted" data-attribution="">
      <a href={TMDB_URL} target="_blank" rel="noopener noreferrer">
        {t("media.match.source")}
      </a>{" "}
      <span lang="en">{t("media.match.attribution")}</span>
    </p>
  );
}
