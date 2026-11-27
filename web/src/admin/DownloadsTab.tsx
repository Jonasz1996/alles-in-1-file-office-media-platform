// /beheer/downloaden: "Film/serie downloaden" (fase 11, fixlijst 26-11). Wat eerst onderaan
// /beheer/media stond, staat nu in een eigen tabblad: films en series volgen (met de kalender en de
// lopende downloads), de koppeling met Prowlarr en qBittorrent, en de verzoeken van gezinsleden.
// Beheer.tsx toont het tabblad met dezelfde voorwaarde als Media; Alt+0 springt erheen.

import { type JSX, useEffect, useState } from "react";
import { errorText, t } from "../i18n";
import { Link } from "../links";
import { getAdmin } from "../media/api";
import type { Library } from "../media/types";
import ArrBlock from "./ArrBlock";
import RequestsSettings from "./RequestsSettings";
import WantedBlock from "./WantedBlock";

export default function DownloadsTab(): JSX.Element {
  const [libraries, setLibraries] = useState<Library[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    getAdmin()
      .then((a) => alive && setLibraries(a.libraries))
      .catch((e: unknown) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section aria-labelledby="admin-downloads-title">
      <h2 id="admin-downloads-title">{t("admin.downloads.title")}</h2>
      <p className="muted">
        {t("admin.downloads.intro")} <Link to="/beheer/media">{t("admin.downloads.toMedia")}</Link>
      </p>
      {error !== null && <p role="alert">{t("admin.refreshFailed", { error })}</p>}
      {libraries === null && error === null && <p className="muted">{t("admin.media.loading")}</p>}
      {libraries !== null && (
        <>
          <WantedBlock libraries={libraries} />
          <ArrBlock />
          <RequestsSettings libraries={libraries} />
        </>
      )}
    </section>
  );
}
