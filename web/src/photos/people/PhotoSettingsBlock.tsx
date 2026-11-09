// Het blok Foto's onder Account (fase 6, van Z6; Keuze 44 en 81): de plek in je foto's gebruiken
// (mij of uit) en herinneringen tonen, plus de plekken voor gezichten (X2, FacesSwitch) en zoeken
// op beeld (X3, BeeldSwitch). Een eigen chunk met het gebied photos_people; Account laadt hem lazy.
//
// Gezichten en beeld: zolang X2 of X3 zijn schakelaar niet vult (de plek blijft leeg), staat hier
// een eigen vinkje. Aanzetten gaat naar PATCH /me/photo-settings; zonder X2, X3 of de analyse
// weigert de API dat (409, of 403 als de beheerder het verbiedt). Dan staat de reden onder het
// vinkje en blijft het uit; de rest van de pagina werkt gewoon door. Vult X2 of X3 de plek, dan
// verdwijnt het eigen vinkje.
//
// X1 (Keuze 23): "Tekst in foto's herkennen" (ocr_photos, standaard uit) is een gewoon vinkje
// hier, zoals de plek en herinneringen: de tekst blijft bij jouw foto's, alleen jij vindt hem.
//
// Toetsenbord: Tab door de vinkjes, Spatie zet aan of uit.

import { type JSX, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { errorText, lazyWithAreas, t } from "../../i18n";
import { LazySlot } from "../../ui/LazySlot";
import { toast } from "../../ui/toast";
import { photoSettings, updatePhotoSettings } from "./api";
import type { PhotoSettings, PhotoSettingsPatch } from "./types";
import "./people.css";

const FacesSwitch = lazyWithAreas(() => import("../faces/FacesSwitch"), ["photos_faces"]);
const BeeldSwitch = lazyWithAreas(() => import("../../search/beeld/BeeldSwitch"), ["beeld"]);

type Switch = "faces" | "beeld";

export default function PhotoSettingsBlock(): JSX.Element {
  const id = useId();
  const [s, setS] = useState<PhotoSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState<Partial<Record<Switch, string>>>({});

  useEffect(() => {
    let live = true;
    photoSettings().then(
      (got) => live && setS(got),
      (e) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, []);

  async function change(patch: PhotoSettingsPatch, which?: Switch) {
    try {
      setS(await updatePhotoSettings(patch));
      if (which) setRefused((r) => ({ ...r, [which]: undefined }));
      toast(t("photos_people.settings.saved"), { kind: "ok" });
    } catch (e) {
      // Geweigerd: het vinkje blijft zoals het was (s verandert niet), met de reden eronder
      if (which) setRefused((r) => ({ ...r, [which]: errorText(e) }));
      else toast(errorText(e), { kind: "error" });
    }
  }

  return (
    <section aria-labelledby={`${id}-kop`} className="photo-settings">
      <h2 id={`${id}-kop`}>{t("photos_people.settings.title")}</h2>
      {error && <p role="alert">{error}</p>}
      {s && (
        <>
          <Check
            label={t("photos_people.settings.location")}
            hint={t("photos_people.settings.locationHint")}
            checked={s.location === "mij"}
            onChange={(on) => void change({ location: on ? "mij" : "uit" })}
          />
          <Check
            label={t("photos_people.settings.memories")}
            checked={s.memories}
            onChange={(on) => void change({ memories: on })}
          />
          <Check
            label={t("photos_people.settings.ocr")}
            hint={t("photos_people.settings.ocrHint")}
            checked={s.ocr_photos}
            onChange={(on) => void change({ ocr_photos: on })}
          />
          <Slot
            fallback={
              <Check
                label={t("photos_people.settings.faces")}
                hint={s.faces_available ? undefined : t("photos_people.settings.notYet")}
                error={refused.faces}
                checked={s.faces === "aan"}
                onChange={(on) => void change({ faces: on ? "aan" : "uit" }, "faces")}
              />
            }
          >
            <FacesSwitch />
          </Slot>
          <Slot
            fallback={
              <Check
                label={t("photos_people.settings.beeld")}
                hint={t("photos_people.settings.beeldHint")}
                error={refused.beeld}
                checked={s.beeld === "aan"}
                onChange={(on) => void change({ beeld: on ? "aan" : "uit" }, "beeld")}
              />
            }
          >
            <BeeldSwitch />
          </Slot>
        </>
      )}
    </section>
  );
}

/** Een vinkje met uitleg en (na een weigering) de reden. */
function Check({
  label,
  hint,
  error,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  error?: string;
  checked: boolean;
  onChange(on: boolean): void;
}): JSX.Element {
  const id = useId();
  const described = [hint ? `${id}-uitleg` : null, error ? `${id}-fout` : null].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label className="check">
        <input
          type="checkbox"
          checked={checked}
          aria-describedby={described || undefined}
          aria-invalid={error ? true : undefined}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </label>
      {hint && (
        <small id={`${id}-uitleg`} className="muted">
          {hint}
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

/** De plek van X2 of X3; zolang die niets toont, het eigen vinkje (fallback). */
function Slot({ children, fallback }: { children: ReactNode; fallback: ReactNode }): JSX.Element {
  const box = useRef<HTMLDivElement>(null);
  const [filled, setFilled] = useState(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const look = () => setFilled(el.childElementCount > 0);
    look();
    const watch = new MutationObserver(look);
    watch.observe(el, { childList: true });
    return () => watch.disconnect();
  }, []);

  return (
    <>
      <div ref={box} className="photo-switch">
        <LazySlot>{children}</LazySlot>
      </div>
      {!filled && fallback}
    </>
  );
}
