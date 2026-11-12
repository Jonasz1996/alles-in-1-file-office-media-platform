// Gezichten in het infopaneel (fase 6, van X2; Keuze 66): de kaders van de gevonden gezichten op
// een eigen foto, met een naam of "?", en "?" opent "Wie is dit?". Naast de rij Personen van Z6
// (PeopleRow.tsx). Z0b zette de plek (review contract F3): InfoPanel.tsx laadt dit bestand lazy,
// met het gebied photos_faces, alleen bij een foto (showFacesRow). Een ander die de foto mag zien,
// krijgt van GET /files/{id}/faces []. Tot X2 het vult, toont het niets. Het contract: de default
// export met InfoRowProps, een eigen chunk.

import type { InfoRowProps } from "../../viewer/infoRows";

export default function FacesRow(_: InfoRowProps): null {
  return null;
}
