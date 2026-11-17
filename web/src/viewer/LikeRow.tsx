// De rij "Meer zoals deze" in het infopaneel (fase 6, van X3; Keuze 78, 79 en 81): vanuit een eigen
// foto naar /zoeken?mode=image&like=<node>, alleen als zoeken op beeld voor jou aan staat. Z0b zette
// de plek (Keuze 55). Het contract: de default export met InfoRowProps, een eigen chunk, met het
// gebied beeld (≤ 3 kB samen, budget.mjs).
//
// Eerst de stand (GET /photos/beeld, gedeeld met het raster in search/beeld/status.ts): alleen bij
// ok of bezig. Met de analyse uit (501, de stand off), uit bij jou of bij de beheerder, of zonder
// model of container toont de rij niets, zoals in 0.8.0. Dan of de foto van jou is (GET
// /files/{id}, permission; zoals PeopleRow): "Meer zoals deze" zoekt alleen in je eigen foto's.
// Is de foto nog niet bekeken, dan zegt /zoeken dat (beeld_no_vector).
//
// Toetsenbord: Tab naar "Meer zoals deze", Enter opent /zoeken met de foto's die erop lijken.

import { type JSX, useEffect, useId, useState } from "react";
import { api, type FileNode } from "../api";
import { useCurrentUser } from "../currentUser";
import { t } from "../i18n";
import { Link } from "../links";
import { canLike, likeHref, useBeeld } from "../search/beeld/status";
import type { InfoRowProps } from "./infoRows";

export default function LikeRow({ item }: InfoRowProps): JSX.Element | null {
  const user = useCurrentUser();
  const known = useBeeld(user?.id ?? null);
  const can = canLike(known);
  /** Het id van de foto waarvan vaststaat dat hij van jou is */
  const [own, setOwn] = useState<string | null>(null);

  useEffect(() => {
    if (!can) return;
    let live = true;
    api.request<FileNode>("GET", `/api/v1/files/${encodeURIComponent(item.id)}`).then(
      // Zoals permissionOf (sharing.ts): zonder permission is het van jou
      (node) => live && setOwn((node.permission ?? "owner") === "owner" ? item.id : null),
      () => live && setOwn(null),
    );
    return () => {
      live = false;
    };
  }, [can, item.id]);

  if (!can || own !== item.id) return null;
  return <LikeSection id={item.id} />;
}

/** De rij zelf: een kop en de link naar /zoeken?mode=image&like=<id>. */
export function LikeSection({ id }: { id: string }): JSX.Element {
  const headingId = useId();
  return (
    <section className="like-row" aria-labelledby={headingId}>
      <h4 id={headingId}>{t("beeld.title")}</h4>
      <p>
        <Link to={likeHref(id)}>{t("beeld.like")}</Link>
      </p>
    </section>
  );
}
