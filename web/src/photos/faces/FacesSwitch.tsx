// De schakelaar "Gezichten herkennen in mijn foto's" met de dialogen bij aan- en uitzetten (fase
// 6, X2b; Keuze 64 en 66), in het blok Foto's onder Account (van Z6). Z0b zette de plek (review
// contract F3); Z6 laadt dit bestand lazy in zijn blok, met het gebied photos_faces. Aanzetten
// vraagt eerst toestemming met de tekst van Keuze 66; uitzetten zegt dat alles gewist wordt. Het
// gaat via PATCH /me/photo-settings (updatePhotoSettings). Het contract: de default export zonder
// props, een eigen chunk.
//
// Zonder de analyse (faces_available false) toont dit niets: dan blijft het eigen vinkje van Z6
// staan, precies zoals in 0.8.0. Toetsenbord: Tab naar het vinkje, Spatie opent de dialoog; in de
// dialoog staat de focus eerst op Annuleren.

import { type JSX, useEffect, useId, useState } from "react";
import { useCurrentUser } from "../../currentUser";
import { errorText, t } from "../../i18n";
import { confirm } from "../../ui/dialogs";
import { toast } from "../../ui/toast";
import type { PhotoSettings } from "../people/types";
import { available, facesSettings, peopleApi, rememberSettings } from "./logic";

export default function FacesSwitch(): JSX.Element | null {
  const id = useId();
  const [s, setS] = useState<PhotoSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const who = useCurrentUser()?.id ?? "";

  useEffect(() => {
    facesSettings(who).then(setS, () => {});
  }, [who]);

  if (!s || !available(s)) return null;
  const on = s.faces === "aan";

  async function change(want: boolean) {
    const ok = await confirm(
      want
        ? {
            title: t("photos_faces.switch.label"),
            message: t("photos_faces.switch.consent"),
            confirm: t("photos_faces.switch.on"),
          }
        : {
            title: t("photos_faces.switch.offTitle"),
            message: t("photos_faces.switch.offText"),
            confirm: t("photos_faces.switch.off"),
            danger: true,
          },
    );
    if (!ok) return;
    try {
      const next = await (await peopleApi()).updatePhotoSettings({ faces: want ? "aan" : "uit" });
      rememberSettings(who, next);
      setS(next);
      setError(null);
      toast(t(want ? "photos_faces.switch.turnedOn" : "photos_faces.switch.turnedOff"), { kind: "ok" });
    } catch (e) {
      setError(errorText(e)); // geweigerd (403 faces_forbidden, 409 faces_unavailable): blijft zoals het was
    }
  }

  const hint = s.faces_forbidden ? `${id}-uitleg` : null;
  const described = [hint, error ? `${id}-fout` : null].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label className="check">
        <input
          type="checkbox"
          checked={on}
          aria-describedby={described || undefined}
          aria-invalid={error ? true : undefined}
          data-faces="switch"
          onChange={(e) => void change(e.target.checked)}
        />
        {t("photos_faces.switch.label")}
      </label>
      {hint && (
        <small id={hint} className="muted">
          {t("photos_faces.status.forbidden")}
        </small>
      )}
      {error && (
        <p id={`${id}-fout`} className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
