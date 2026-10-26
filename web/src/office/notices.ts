// De meldingen bij office.unsaved en office.recovered (met een link naar de herstelkopie). De
// luisteraar in App.tsx laadt dit pas bij de eerste gebeurtenis office.* (Keuze 33), samen met
// het gebied office, en geeft daarna elke gebeurtenis office.* hier door.
//
// Van O2. O0 zet dit als stub met de vaste vorm: handle() doet nog niets.

import type { ServerEvent } from "../events";

export function handle(_event: ServerEvent): void {}
