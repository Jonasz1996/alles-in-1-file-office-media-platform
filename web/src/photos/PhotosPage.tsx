// Plaatshouder (P0) voor de fototijdlijn van P5 (/fotos).
import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";

export default function PhotosPage(_: RouteProps): JSX.Element {
  return (
    <main className="wide">
      <PageTitle>{t("photos.title")}</PageTitle>
      <EmptyState icon="photo" title={t("common.comingSoon")}>
        <p>{t("photos.timelineSoon")}</p>
      </EmptyState>
    </main>
  );
}
