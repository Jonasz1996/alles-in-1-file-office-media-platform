// Plaatshouder (P0) voor P5: een album achter een publieke link (/s/<token>, kind "album").
import type { JSX } from "react";
import type { PublicShare } from "../api";
import { t } from "../i18n";
import type { PublicLink } from "../publicApi";
import { EmptyState } from "../ui/EmptyState";

export interface PublicAlbumProps {
  link: PublicLink;
  share: PublicShare;
}

export default function PublicAlbum(_: PublicAlbumProps): JSX.Element {
  return (
    <EmptyState icon="album" title={t("common.comingSoon")}>
      <p>{t("photos.publicAlbumSoon")}</p>
    </EmptyState>
  );
}
