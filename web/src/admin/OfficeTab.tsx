// /beheer/office: hoe office ervoor staat, de grens, en niet-opgeslagen werk terugzetten.
//
// Van O5 (bouwontwerp §3.5, O5). O0 zet dit als stub: alleen de tekst dat het overzicht later
// komt. Beheer.tsx toont het tabblad alleen als office ingesteld is.

import type { JSX } from "react";
import { t } from "../i18n";

export function OfficeTab(): JSX.Element {
  return <p>{t("admin.office.notReady")}</p>;
}
