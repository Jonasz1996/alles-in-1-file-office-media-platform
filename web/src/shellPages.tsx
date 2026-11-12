// De pagina's van de schil zelf: een adres dat niet bestaat, en een pagina waar je rol niet bij
// mag. Allebei met alleen het woordenboek "shell" (altijd geladen).

import type { JSX } from "react";
import { t } from "./i18n";
import { Link } from "./links";
import type { RouteProps } from "./router";
import { EmptyState } from "./ui/EmptyState";
import { PageTitle } from "./ui/PageTitle";

/** Elk adres dat in de routetabel niet bestaat (*). */
export default function NotFound(_: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("shell.notFound.title")}</PageTitle>
      <EmptyState icon="warning" title={t("shell.notFound.text")} action={<Link to="/">{t("shell.notFound.home")}</Link>} />
    </main>
  );
}

/** Een route die bestaat, maar niet voor jouw rol (bijv. Beheer voor wie geen beheerder is). */
export function NoAccess(): JSX.Element {
  return (
    <main>
      <PageTitle>{t("shell.noAccess.title")}</PageTitle>
      <EmptyState
        icon="error"
        title={t("shell.noAccess.text")}
        action={<Link to="/">{t("shell.notFound.home")}</Link>}
      />
    </main>
  );
}
