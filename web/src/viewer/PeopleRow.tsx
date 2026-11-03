// De rij Personen in het infopaneel (fase 6, van Z6; Keuze 37): wie er op de foto staat, en
// "Persoon toevoegen" met een kader, alleen voor de eigenaar (GET /files/{id}/people; voor een
// ander []). Z0 zette de plek; tot Z6 hem vult, toont hij niets. Het contract: de default export
// met InfoRowProps, een eigen chunk, met het gebied photos_people.

import type { InfoRowProps } from "./infoRows";

export default function PeopleRow(_: InfoRowProps): null {
  return null;
}
