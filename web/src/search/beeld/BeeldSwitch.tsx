// De regel "Zoeken op beeld" in het blok Foto's onder Account (fase 6, van X3; Keuze 81: "Z6 (het
// blok), X3 (de regel)"). Z0b zette de plek (review contract F3); Z6 laadt dit bestand lazy in
// zijn blok, met het gebied beeld. Het contract: de default export zonder props, een eigen chunk.
//
// Zolang deze plek niets toont, staat in het blok het eigen vinkje van Z6 (PhotoSettingsBlock,
// Slot): precies wat 0.8.0 toonde. Zo blijft het met de analyse uit (GET /photos/beeld geeft 501:
// de stand off), en ook zolang de stand of je instellingen er nog niet zijn. Kent de server zoeken
// op beeld, dan staat hier de schakelaar met de uitleg van Keuze 81 (standaard uit, Vraag 11);
// aanzetten gaat naar PATCH /me/photo-settings. Weigert de API (409 beeld_off van de beheerder,
// 409 beeld_not_installed), dan staat de reden eronder en blijft het vinkje zoals het was. Na een
// wijziging vergeet status.ts de stand, zodat /zoeken en "Meer zoals deze" hem opnieuw vragen.
//
// Toetsenbord: Tab naar het vinkje, Spatie zet aan of uit.

import { type JSX, useEffect, useId, useState } from "react";
import { useCurrentUser } from "../../currentUser";
import { errorText, t } from "../../i18n";
import { photoSettings, updatePhotoSettings } from "../../photos/people/api";
import type { OnOff } from "../../photos/people/types";
import { toast } from "../../ui/toast";
import { forgetBeeld, type Known, useBeeld } from "./status";

/** Of de schakelaar er staat: alleen als de server zoeken op beeld kent. */
export function showSwitch(known: Known | null): boolean {
  return known !== null && known.state !== "off" && known.state !== "fout";
}

export default function BeeldSwitch(): JSX.Element | null {
  const user = useCurrentUser();
  const known = useBeeld(user?.id ?? null);
  const show = showSwitch(known);
  const [beeld, setBeeld] = useState<OnOff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!show) return;
    let live = true;
    photoSettings().then(
      (s) => live && setBeeld(s.beeld),
      () => {},
    );
    return () => {
      live = false;
    };
  }, [show]);

  if (!show || !known || beeld === null) return null;

  async function change(on: boolean): Promise<void> {
    try {
      const s = await updatePhotoSettings({ beeld: on ? "aan" : "uit" });
      setBeeld(s.beeld);
      setError(null);
      forgetBeeld();
      toast(t("beeld.switch.saved"), { kind: "ok" });
    } catch (e) {
      // Geweigerd: het vinkje blijft zoals het was, met de reden eronder
      setError(errorText(e));
    }
  }

  return <BeeldCheck known={known} on={beeld === "aan"} error={error} onChange={(on) => void change(on)} />;
}

/** Het vinkje met de uitleg, de stand van de beheerder en (na een weigering) de reden. */
export function BeeldCheck({
  known,
  on,
  error,
  onChange,
}: {
  known: Known;
  on: boolean;
  error: string | null;
  onChange(on: boolean): void;
}): JSX.Element {
  const id = useId();
  const admin = known.state === "beheer_uit";
  const described = [`${id}-uitleg`, admin ? `${id}-beheer` : null, error ? `${id}-fout` : null].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label className="check">
        <input
          type="checkbox"
          checked={on}
          aria-describedby={described}
          aria-invalid={error ? true : undefined}
          onChange={(e) => onChange(e.target.checked)}
        />
        {t("beeld.switch.label")}
      </label>
      <small id={`${id}-uitleg`} className="muted">
        {t("beeld.switch.hint")}
      </small>
      {admin && (
        <small id={`${id}-beheer`} className="muted">
          {t("beeld.notice.offAdmin")}
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
