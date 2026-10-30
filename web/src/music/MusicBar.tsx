// De muziekbalk (fase 5, van M9; Keuze 37): onderaan de schil, met hoes, titel, spelen, vorige,
// volgende, tijdlijn, volume en de rij. App.tsx laadt dit chunk (en het gebied "music") pas als
// musicBar.visible() waar is, en houdt het buiten de pagina: hij blijft staan bij navigeren.
// M9 zet hem aan met musicBar.show() en weg met musicBar.hide() (media/index.ts).
//
// M0 zette een lege versie: een lege regio met het label. M9 maakt de balk.

import type { JSX } from "react";
import { t } from "../i18n";

export default function MusicBar(): JSX.Element {
  return <section className="music-bar" aria-label={t("music.bar.label")} />;
}
