// De rij Plek in het infopaneel (fase 6, van Z5; Keuze 27–30): de plaatsnaam en een kleine kaart,
// alleen voor de eigenaar (GET /files/{id}/location; een ander krijgt 404). Z0 zette de plek; tot
// Z5 hem vult, toont hij niets. Het contract: de default export met InfoRowProps, een eigen chunk,
// met het gebied photos_map.

import type { InfoRowProps } from "./infoRows";

export default function LocationRow(_: InfoRowProps): null {
  return null;
}
