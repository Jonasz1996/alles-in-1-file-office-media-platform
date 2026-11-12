// De speler in de viewer van Bestanden (fase 5, van M6). viewer/MediaView.tsx (de haak van M0)
// laadt dit chunk voor een video als media aan staat, en geeft de <video> van fase 3 mee als
// fallback: voor als de speler het niet kan (geen MSE en geen bestand, of play zegt nee aan het
// begin). In de viewer krijgt de speler alleen Spatie, K, J, L, M, C, A, P en 0–9 (Keuze 33); de
// rest blijft van de viewer.

import { type JSX, useState } from "react";
import { t, useAreas } from "../i18n";
import { Spinner } from "../ui/Spinner";
import type { ViewerPlayerProps } from "../viewer/MediaView";
import { sizeOf } from "./caps";
import Player from "./ui/Player";

export { sizeOf };

export default function ViewerPlayer({ item, detail, fallback }: ViewerPlayerProps): JSX.Element {
  const ready = useAreas(["player"]);
  // Ook zonder MSE de speler (direct of native HLS, met de beslissing van de server); pas als
  // play aan het begin nee zegt, of de browser het niet kan, de fallback
  const [failed, setFailed] = useState(false);
  if (failed) return fallback();
  if (!ready) {
    return (
      <div className="media media-video">
        <Spinner label={t("viewer.media.loadingPlayer")} />
      </div>
    );
  }
  return (
    <div className="media player-host">
      <Player
        nodeId={item.id}
        where="viewer"
        title={detail.meta.title ?? item.name}
        poster={detail.large}
        size={sizeOf(detail.meta)}
        onFallback={() => setFailed(true)}
      />
    </div>
  );
}
