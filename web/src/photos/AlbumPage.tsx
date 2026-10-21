// Plaatshouder (P0) voor één album van P5 (/album/:id).
import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";

export default function AlbumPage(_: RouteProps): JSX.Element {
  return (
    <main className="wide">
      <PageTitle>{t("photos.album")}</PageTitle>
      <EmptyState icon="album" title={t("common.comingSoon")}>
        <p>{t("photos.albumsSoon")}</p>
      </EmptyState>
    </main>
  );
}
