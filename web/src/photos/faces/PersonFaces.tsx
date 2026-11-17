// Gezichten op de pagina van een persoon (fase 6, X2b; Keuze 66): het blok "Misschien ook Anna",
// met zes uitsneden en een knop die het raster met alle voorstellen voor deze persoon opent. Z0b
// zette de plek (review contract F3): PersonPage.tsx van Z6 laadt dit bestand lazy, met het
// gebied photos_faces. Het contract: de default export met PersonFacesProps, een eigen chunk.
// Zonder de analyse, met gezichten uit, of zonder voorstellen: niets (zoals 0.8.0). Maakte het
// raster de laatste voorstellen op, dan staat er "Hier staat niets meer.", met de focus erop.

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { useCurrentUser } from "../../currentUser";
import { useReloadOn } from "../../events";
import { t } from "../../i18n";
import { PEOPLE_CHANGED } from "../people/types";
import { personFaceSuggestions } from "./api";
import { FaceCard, FaceGrid } from "./FaceGrid";
import { personSample } from "./logic";
import { FACES_PAGE, type FacePage } from "./types";
import "./faces.css";

export interface PersonFacesProps {
  /** De persoon van de pagina (params.id van /fotos/personen/:id). */
  personId: string;
}

const focusNow = (el: HTMLElement | null) => el?.focus();

export default function PersonFaces({ personId }: PersonFacesProps): JSX.Element | null {
  const [page, setPage] = useState<FacePage | null>(null);
  const [open, setOpen] = useState(""); // de naam, zolang het raster open is
  const [done, setDone] = useState("");
  const button = useRef<HTMLButtonElement | null>(null);
  const who = useCurrentUser()?.id ?? "";

  const load = useCallback(async () => {
    try {
      const next = await personSample(who, personId);
      setPage(next);
      return next?.faces.length;
    } catch {
      setPage(null); // geen voorstellen te tonen; de pagina zelf werkt door
    }
  }, [who, personId]);

  useEffect(() => {
    void load();
  }, [load]);
  useReloadOn((e) => e.type === PEOPLE_CHANGED, () => void load());

  // Zolang het raster open is, blijft het staan, ook als een nieuwe lading niets meer geeft
  const name = open || page?.faces[0]?.suggest?.name || done;
  if (!name) return null;
  const title = t("photos_faces.suggest.maybe", { name });
  return (
    <section className="person-faces" aria-labelledby="faces-misschien">
      <h2 id="faces-misschien">{title}</h2>
      {open ? (
        <FaceGrid
          title={t("photos_faces.suggest.question", { name })}
          mode="suggest"
          person={{ id: personId, name }}
          load={(c) => personFaceSuggestions(personId, c, FACES_PAGE)}
          onClose={() => {
            setOpen("");
            requestAnimationFrame(() => button.current?.focus());
            void load().then((n) => n || setDone(name));
          }}
        />
      ) : !page?.faces.length ? (
        <p className="muted" tabIndex={-1} ref={focusNow}>
          {t("photos_faces.grid.empty")}
        </p>
      ) : (
        <FaceCard
          faces={page.faces}
          label={t("photos_faces.grid.open")}
          open={false}
          onOpen={() => setOpen(name)}
          button={(el) => {
            button.current = el;
          }}
        />
      )}
    </section>
  );
}
