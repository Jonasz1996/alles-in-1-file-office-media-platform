// De waarschuwing in het deeldialoog (fase 6, van Z5; Keuze 30): "N foto's hebben een plek in het
// bestand; wie ze krijgt, kan die zien", ook bij "onbekend", bij elke soort deling (personen,
// groepen, een publieke link; een map, een album of een los bestand). Z0 zette de plek; tot Z5 hem
// vult, toont hij niets. Het contract: de default export, een eigen chunk met het gebied
// photos_map, pas geladen als het dialoog opengaat (ShareDialogPanel.tsx). De rij nodes is bij
// elke render een nieuwe: kijk naar de ids, niet naar de rij zelf.

import type { FileNode } from "../api";

export interface LocationWarningProps {
  /** Wat gedeeld wordt: één node, of de selectie die gedeeld kan worden */
  nodes: readonly FileNode[];
}

export default function LocationWarning(_: LocationWarningProps): null {
  return null;
}
