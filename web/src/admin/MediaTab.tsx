// /beheer/media (fase 5, van M10; Keuze 45): de worker, ffmpeg, het budget, de sessies, de
// achtergrondbaan, de bibliotheken, de cache, de providers, de import en MediaConfig.
// Beheer.tsx (de haak van M0) toont het tabblad alleen als GET /api/v1/admin/media antwoordt,
// en laadt dit chunk pas bij het tabblad zelf.
//
// M0 zette een lege versie; M10 maakt het tabblad.

import type { JSX } from "react";
import { t } from "../i18n";
import { EmptyState } from "../ui/EmptyState";

export default function MediaTab(): JSX.Element {
  return <EmptyState icon="settings" title={t("admin.media.notReady")} />;
}
