// Plaatshouder (P0) voor P6: alles wat je deelt (/gedeeld/door-mij).
import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";

export default function MyShares(_: RouteProps): JSX.Element {
  return (
    <main className="wide">
      <PageTitle>{t("sharing.myShares")}</PageTitle>
      <EmptyState icon="share" title={t("common.comingSoon")}>
        <p>{t("sharing.mySharesSoon")}</p>
      </EmptyState>
    </main>
  );
}
