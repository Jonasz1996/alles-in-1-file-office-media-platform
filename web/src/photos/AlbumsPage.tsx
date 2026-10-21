// Plaatshouder (P0) voor de albums van P5 (/albums).
import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";

export default function AlbumsPage(_: RouteProps): JSX.Element {
  return (
    <main className="wide">
      <PageTitle>{t("photos.albums")}</PageTitle>
      <EmptyState icon="album" title={t("common.comingSoon")}>
        <p>{t("photos.albumsSoon")}</p>
      </EmptyState>
    </main>
  );
}
