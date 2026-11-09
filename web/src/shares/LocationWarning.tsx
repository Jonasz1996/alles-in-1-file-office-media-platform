// De waarschuwing in het deeldialoog (fase 6, van Z5; Keuze 30): "12 foto's hierin hebben een
// locatie in het bestand. Wie ze downloadt, krijgt die mee." en zo nodig "Van 3 video's weten we
// het niet (media staat uit)." Bij elke soort deling: het dialoog is hetzelfde voor een persoon,
// een groep, een gast en een publieke link, op een map, een album of een los bestand, en bij een
// selectie (de items die gedeeld kunnen worden).
//
// Een eigen chunk met het gebied photos_map, pas geladen als het dialoog opengaat
// (ShareDialogPanel.tsx, in een LazySlot). Het dialoog wacht er nooit op: tot het antwoord er is,
// staat hier niets. Komt er een zin, dan staat hij bovenaan in het dialoog en leest een
// schermlezer hem voor (announce: de focus blijft waar hij was, bijv. in het zoekveld). De rij nodes
// is bij elke render een nieuwe: het effect kijkt naar de ids.

import { type JSX, useEffect, useState } from "react";
import type { FileNode } from "../api";
import { getShareCheck, mayHoldPlace, subjectOf, tallyChecks, warningLines } from "../photos/map/share";
import { announce } from "../ui/toast";
import "../photos/map/share.css";

export interface LocationWarningProps {
  /** Wat gedeeld wordt: één node, of de selectie die gedeeld kan worden */
  nodes: readonly FileNode[];
}

export default function LocationWarning({ nodes }: LocationWarningProps): JSX.Element | null {
  const ids = nodes
    .filter(mayHoldPlace)
    .map((n) => n.id)
    .join(",");
  const subject = subjectOf(nodes);
  const [lines, setLines] = useState<readonly string[]>([]);

  useEffect(() => {
    setLines([]);
    if (ids === "") return;
    const ctl = new AbortController();
    tallyChecks(ids.split(","), getShareCheck, ctl.signal).then(
      (tally) => {
        const said = warningLines(tally, subject);
        setLines(said);
        if (said.length > 0) announce(said.join(" "));
      },
      () => {}, // afgebroken: het dialoog is dicht of deelt nu iets anders
    );
    return () => ctl.abort();
  }, [ids, subject]);

  return <LocationWarningText lines={lines} />;
}

/** De zinnen zelf (ook voor de test): niets als er niets te zeggen is. */
export function LocationWarningText({ lines }: { lines: readonly string[] }): JSX.Element | null {
  if (lines.length === 0) return null;
  return (
    <div className="share-place-warning" role="note">
      {lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </div>
  );
}
