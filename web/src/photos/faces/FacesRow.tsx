// Gezichten in het infopaneel (fase 6, X2b; Keuze 66): de kaders van de gevonden gezichten op een
// eigen foto, met een naam of "?"; "?" opent "Wie is dit?" met het voorstel ingevuld maar niet
// bevestigd. Naast de rij Personen van Z6 (PeopleRow.tsx). Z0b zette de plek (review contract
// F3): InfoPanel.tsx laadt dit bestand lazy, met het gebied photos_faces, alleen bij een foto
// (showFacesRow). Het contract: de default export met InfoRowProps, een eigen chunk.
//
// Alleen voor de eigenaar met gezichten aan; een ander krijgt van GET /files/{id}/faces [] en ziet
// niets. Zonder de analyse vraagt de rij geen route van gezichten en toont hij niets (0.8.0).
// Toetsenbord: Tab naar "?" (Wie is dit?), Enter opent het formulier; Esc sluit het en zet de
// focus terug op "?". De viewer bladert niet zolang het formulier open is.

import { type JSX, type KeyboardEvent, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useCurrentUser } from "../../currentUser";
import { useServerEvents } from "../../events";
import { errorText, loadAreas, t } from "../../i18n";
import { Link } from "../../links";
import { Button } from "../../ui/Button";
import { toast } from "../../ui/toast";
import type { InfoRowProps } from "../../viewer/infoRows";
import type { ComboOption } from "../people/combo";
import { PEOPLE_CHANGED, type Person } from "../people/types";
import { confirmFaces, hideFaces, rejectFaces, rescanFaces } from "./api";
import { PersonCombo, peopleApi, rowFaces } from "./logic";
import type { FaceOut } from "./types";
import "./faces.css";

export default function FacesRow({ item, detail }: InfoRowProps): JSX.Element | null {
  const [faces, setFaces] = useState<FaceOut[] | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [mine, setMine] = useState<Person[]>([]);
  const buttons = useRef(new Map<string, HTMLButtonElement | null>());
  const fileId = item.id;
  const who = useCurrentUser()?.id ?? "";

  const load = useCallback(async () => {
    try {
      setFaces(await rowFaces(who, fileId));
    } catch {
      setFaces(null); // weg, uit of geen verbinding: dan geen rij
    }
  }, [who, fileId]);

  useEffect(() => {
    void load();
  }, [load]);
  useServerEvents((e) => {
    if (e.type === PEOPLE_CHANGED) void load();
  });

  if (!faces) return null;

  async function ask(f: FaceOut) {
    await loadAreas(["photos_people"]); // de teksten van PersonCombo
    peopleApi()
      .then((m) => m.people())
      .then((r) => setMine(r.people), () => {});
    setAsking(f.id);
  }

  function close(faceId: string) {
    setAsking(null);
    requestAnimationFrame(() => buttons.current.get(faceId)?.focus());
  }

  async function act(run: () => Promise<void>, text: string) {
    try {
      await run();
      toast(text, { kind: "ok" });
      setAsking(null);
      await load();
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  const unknown = faces.filter((f) => !f.person);
  return (
    <section className="faces-row" aria-labelledby={`gezichten-${fileId}`}>
      <h4 id={`gezichten-${fileId}`}>{t("photos_faces.row.title")}</h4>
      {faces.length === 0 ? (
        <p className="muted">{t("photos_faces.row.none")}</p>
      ) : (
        <>
          {detail?.thumb && (
            <div className="faces-frames" aria-hidden="true">
              <img src={detail.thumb} alt="" />
              {faces.map((f) => (
                <span
                  key={f.id}
                  className={f.person ? "named" : undefined}
                  style={{
                    left: `${f.box[0] * 100}%`,
                    top: `${f.box[1] * 100}%`,
                    width: `${f.box[2] * 100}%`,
                    height: `${f.box[3] * 100}%`,
                  }}
                >
                  {f.person ? f.person.name : t("photos_faces.row.unknown")}
                </span>
              ))}
            </div>
          )}
          <ul className="faces-on">
            {faces.map((f) =>
              f.person ? (
                <li key={f.id}>
                  <Link to={`/fotos/personen/${encodeURIComponent(f.person.id)}`}>{f.person.name}</Link>
                </li>
              ) : (
                <li key={f.id}>
                  <button
                    ref={(el) => {
                      buttons.current.set(f.id, el);
                    }}
                    type="button"
                    className="btn"
                    aria-expanded={asking === f.id}
                    aria-label={`${t("photos_faces.row.who")} (${unknown.indexOf(f) + 1}/${unknown.length})`}
                    onClick={() => void ask(f)}
                  >
                    {t("photos_faces.row.unknown")}
                  </button>
                  {asking === f.id && (
                    <WhoForm
                      face={f}
                      people={mine}
                      onClose={() => close(f.id)}
                      onYes={(name) =>
                        f.suggest &&
                        void act(
                          () => confirmFaces({ faces: [f.id], person_id: f.suggest?.person_id ?? "" }),
                          t("photos_faces.done.yes", { n: 1, name }),
                        )
                      }
                      onNo={(name) =>
                        f.suggest &&
                        void act(
                          () => rejectFaces({ faces: [f.id], person_id: f.suggest?.person_id ?? "" }),
                          t("photos_faces.done.no", { name }),
                        )
                      }
                      onHide={() => void act(() => hideFaces([f.id]), t("photos_faces.done.hidden"))}
                      onPick={(o) =>
                        void act(
                          () =>
                            confirmFaces(
                              o.kind === "person"
                                ? { faces: [f.id], person_id: o.person.id }
                                : { faces: [f.id], name: o.name },
                            ),
                          t("photos_faces.done.yes", { n: 1, name: o.kind === "person" ? o.person.name : o.name }),
                        )
                      }
                    />
                  )}
                </li>
              ),
            )}
          </ul>
        </>
      )}
      <Button
        onClick={() =>
          void rescanFaces(fileId).then(
            () => toast(t("photos_faces.row.rescanned"), { kind: "ok" }),
            (e) => toast(errorText(e), { kind: "error" }),
          )
        }
      >
        {t("photos_faces.row.rescan")}
      </Button>
    </section>
  );
}

/** "Wie is dit?": het voorstel (Ja · Nee · Niet tonen) en een naam (een eigen persoon of een
 * nieuwe). Pas Bevestigen of Ja bevestigt. */
function WhoForm(p: {
  face: FaceOut;
  people: readonly Person[];
  onClose(): void;
  onYes(name: string): void;
  onNo(name: string): void;
  onHide(): void;
  onPick(o: ComboOption): void;
}): JSX.Element {
  const [pick, setPick] = useState<ComboOption | null>(null);
  const s = p.face.suggest;

  function onKeyDown(e: KeyboardEvent<HTMLFormElement>) {
    // Esc sluit het formulier, niet de viewer; ← → Home End bladeren niet terwijl het open is
    if (e.key === "Escape" && !e.defaultPrevented) {
      e.preventDefault();
      e.stopPropagation();
      p.onClose();
    } else if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) e.stopPropagation();
  }

  return (
    <form
      className="faces-who"
      aria-label={t("photos_faces.row.who")}
      onKeyDown={onKeyDown}
      onSubmit={(e) => {
        e.preventDefault();
        if (pick) p.onPick(pick);
        else toast(t("photos_faces.group.pick"), { kind: "error" });
      }}
    >
      {s && (
        <p>
          {t("photos_faces.suggest.question", { name: s.name })}{" "}
          <Button onClick={() => p.onYes(s.name)}>{t("photos_faces.suggest.yes")}</Button>{" "}
          <Button onClick={() => p.onNo(s.name)}>{t("photos_faces.suggest.no")}</Button>
        </p>
      )}
      <Suspense fallback={null}>
        <PersonCombo
          label={t("photos_faces.group.name")}
          people={p.people}
          allowNew
          clearOnPick={false}
          autoFocus={!s}
          onPick={setPick}
          onText={() => setPick(null)}
          onEscape={p.onClose}
        />
      </Suspense>
      <p>
        <Button type="submit" variant="primary">
          {t("photos_faces.group.confirm")}
        </Button>{" "}
        <Button onClick={p.onHide}>{t("photos_faces.suggest.hide")}</Button>{" "}
        <Button onClick={p.onClose}>{t("photos_faces.row.cancel")}</Button>
      </p>
    </form>
  );
}
