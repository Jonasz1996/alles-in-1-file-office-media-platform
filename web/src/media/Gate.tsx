// De poort voor elke pagina van fase 5 (M0, frozen; bouwontwerp §3.5). Eerst een antwoord van
// de server; staat media uit (503 media_off, een oudere server), dan zegt de pagina "niet
// gevonden", precies zoals fase 3 bij een adres dat niet bestaat. De pakketten zetten hun pagina
// erin: <MediaGate params={params}>{() => <Pagina … />}</MediaGate>.
//
// NotReady is de inhoud van een lege pagina van M0; elk pakket vervangt hem door zijn eigen.

import type { JSX, ReactNode } from "react";
import { t } from "../i18n";
import NotFound from "../shellPages";
import { EmptyState } from "../ui/EmptyState";
import type { IconName } from "../ui/Icon";
import { PageTitle } from "../ui/PageTitle";
import { useMediaGate } from "./index";

export function MediaGate({
  params,
  children,
}: {
  params: Record<string, string>;
  /** De pagina; pas aangeroepen als media aan staat (zo laadt hij zijn data niet voor niets). */
  children: () => ReactNode;
}): JSX.Element {
  const gate = useMediaGate();
  if (gate === "asking") return <main className="center">{t("common.loading")}</main>;
  if (gate === "off") return <NotFound params={params} />;
  return <>{children()}</>;
}

/** Een lege pagina (M0): de kop en "komt later". */
export function NotReady({ title, text, icon }: { title: string; text: string; icon?: IconName }): JSX.Element {
  return (
    <main>
      <PageTitle>{title}</PageTitle>
      <EmptyState icon={icon ?? "video"} title={text} />
    </main>
  );
}
