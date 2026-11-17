// De kleine luisteraar van App.tsx (Keuze 33, van O0). Hij zit in de eerste lading, dus hij is
// klein en importeert niets van office: pas bij de eerste gebeurtenis office.* laadt hij de
// meldingen (office/notices.ts, van O2) en het gebied office. Daarna gaat elke gebeurtenis
// office.* daarheen, in volgorde. Lukt het laden niet, dan bij de volgende opnieuw.

import type { ServerEvent } from "../events";

export interface NoticesModule {
  handle(event: ServerEvent): void;
}

export function officeListener(load: () => Promise<NoticesModule>): (event: ServerEvent) => void {
  let notices: Promise<NoticesModule> | null = null;
  return (event) => {
    if (!event.type.startsWith("office.")) return;
    const p = (notices ??= load());
    p.then(
      (m) => {
        try {
          m.handle(event);
        } catch (e) {
          console.error(e); // een fout in één melding houdt de volgende niet tegen
        }
      },
      () => {
        if (notices === p) notices = null;
      },
    );
  };
}
