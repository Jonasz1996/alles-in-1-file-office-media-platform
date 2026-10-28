// De poort voor elke pagina van fase 5 (M0, frozen; bouwontwerp §3.5). Eerst een antwoord van
// de server; staat media uit (503 media_off, een oudere server), dan zegt de pagina "niet
// gevonden", precies zoals fase 3 bij een adres dat niet bestaat. De pakketten zetten hun pagina
// erin: <MediaGate params={params}>{() => <Pagina … />}</MediaGate>.
//
// NotReady is de inhoud van een lege pagina van M0; elk pakket vervangt hem door zijn eigen.

import { type JSX, type ReactNode, useEffect, useState } from "react";
import { t } from "../i18n";
import NotFound from "../shellPages";
import { EmptyState } from "../ui/EmptyState";
import type { IconName } from "../ui/Icon";
import { PageTitle } from "../ui/PageTitle";
import { mediaStatus, useMediaStatus } from "./index";

/** Voor een mediapagina: eerst een antwoord van de server. Een verse "aan" geldt meteen; "uit"
 * of een oude status vragen we opnieuw (net aangezet op /beheer?). asking: nog bezig; off: de
 * pagina is er niet (zoals in fase 3: "niet gevonden"); on: media staat aan. Staat hier en niet
 * in index.ts: dan blijft het buiten de eerste lading. */
export function useMediaGate(): "asking" | "off" | "on" {
  const status = useMediaStatus();
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    let live = true;
    const fresh = mediaStatus.peek() !== null && !mediaStatus.stale();
    void (fresh ? Promise.resolve() : mediaStatus.load()).then(() => live && setAsked(true));
    return () => {
      live = false;
    };
  }, []);
  if (!asked && status === null) return "asking";
  return status === null ? "off" : "on";
}

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
