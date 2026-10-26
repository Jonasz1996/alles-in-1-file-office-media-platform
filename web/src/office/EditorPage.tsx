// De editorpagina /bewerken/:id (Keuze 32), een lazy chunk met het gebied office.
//
// Van O2. O0 zet dit als stub: met office ingesteld zegt hij "nog niet klaar"; zonder office (of
// als de server het niet zegt) is het adres er niet, net als in fase 3 (de pagina "niet
// gevonden" van de schil).

import { type JSX, useEffect, useState } from "react";
import { t } from "../i18n";
import { back, type RouteProps } from "../router";
import NotFound from "../shellPages";
import { Button } from "../ui/Button";
import { PageTitle } from "../ui/PageTitle";
import { officeStatus, useOfficeStatus } from "./index";

export default function EditorPage({ params }: RouteProps): JSX.Element {
  const status = useOfficeStatus();
  // Eerst een vers antwoord van de server (ook als dat een fout is)
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    let live = true;
    void officeStatus.load().then(() => live && setAsked(true));
    return () => {
      live = false;
    };
  }, []);
  if (!asked) return <main className="center">{t("common.loading")}</main>;
  if (status === null || status.state === "off") return <NotFound params={params} />;
  return (
    <main>
      <PageTitle>{t("office.title")}</PageTitle>
      <p>{t("office.notReady")}</p>
      <Button onClick={() => back("/")}>{t("office.back")}</Button>
    </main>
  );
}
