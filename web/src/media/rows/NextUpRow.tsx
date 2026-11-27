// De rij Volgende aflevering op /media (fase 5, M7, Keuze 36): per serie met activiteit in de
// laatste 90 dagen de eerstvolgende ongeziene aflevering na de laatst geziene (de server kiest
// ze, GET /media/home). Een aflevering die elders gezien werd, gaat er meteen uit; de volgende
// komt met het opnieuw laden van de start. Dezelfde rij en toetsen als Verder kijken.

import type { JSX } from "react";
import { t } from "../../i18n";
import { applyNextUp } from "../progressSync";
import type { Card } from "../types";
import { ProgressRow } from "./ContinueRow";

export function NextUpRow({ cards }: { cards: Card[] }): JSX.Element | null {
  return <ProgressRow id="next" kind="next" title={t("media.home.nextUp")} cards={cards} apply={applyNextUp} />;
}

export default NextUpRow;
