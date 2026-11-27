// Gezichten op /fotos/personen (fase 6, X2b; Keuze 66): bovenaan de regel met de voortgang (of,
// als gezichten uit staan, één regel met "Lees wat dat betekent"), dan de blokken "Is dit …?" en
// "Wie is dit?", en de knop "Niet benoemde gezichten wissen". Z0b zette de plek (review contract
// F3): PeoplePage.tsx van Z6 laadt dit bestand lazy, met het gebied photos_faces. Het contract:
// de default export zonder props, een eigen chunk.
//
// Zonder de analyse (faces_available false) toont het niets en vraagt het geen route van
// gezichten: de pagina is dan die van 0.8.0. De regel vraagt elke 10 s opnieuw zolang hij te zien
// is en er nog werk is, en verder bij people.changed.

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { useCurrentUser } from "../../currentUser";
import { useReloadOn } from "../../events";
import { errorText, t } from "../../i18n";
import { Link } from "../../links";
import { Button } from "../../ui/Button";
import { confirm } from "../../ui/dialogs";
import { toast } from "../../ui/toast";
import { PEOPLE_CHANGED, type PhotoSettings } from "../people/types";
import { faceGroup, faceGroups, faceSuggestions, facesStatus, personFaceSuggestions, wipeUnnamedFaces } from "./api";
import { FaceCard, FaceGrid } from "./FaceGrid";
import { busy, facesSettings, POLL_MS, peoplePart, statusLine } from "./logic";
import { FACES_PAGE, type FaceGroups, type FacesStatus, type SuggestionsByPerson } from "./types";
import "./faces.css";

type Open = { kind: "person"; id: string; name: string } | { kind: "group"; key: string } | null;

export default function PeopleFaces(): JSX.Element | null {
  const [s, setS] = useState<PhotoSettings | null>(null);
  const who = useCurrentUser()?.id ?? "";
  useEffect(() => {
    facesSettings(who).then(setS, () => {});
  }, [who]);
  const part = s ? peoplePart(s) : "none";
  if (part === "none") return null;
  if (part === "on") return <On />;
  if (part === "forbidden") return <p className="faces-status">{t("photos_faces.status.forbidden")}</p>;
  return (
    <div className="faces-status">
      <p>{t("photos_faces.status.off")}</p>
      <details>
        <summary>{t("photos_faces.status.read")}</summary>
        <p>{t("photos_faces.switch.consent")}</p>
        <p>
          <Link to="/account">{t("photos_faces.status.where")}</Link>
        </p>
      </details>
    </div>
  );
}

function On(): JSX.Element {
  const [status, setStatus] = useState<FacesStatus | null>(null);
  const [suggest, setSuggest] = useState<SuggestionsByPerson | null>(null);
  const [groups, setGroups] = useState<FaceGroups | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [closed, setClosed] = useState(0);
  const buttons = useRef(new Map<string, HTMLButtonElement | null>());
  const wipeButton = useRef<HTMLButtonElement | null>(null);
  const statusRef = useRef<HTMLParagraphElement | null>(null);

  const load = useCallback(async () => {
    try {
      const [st, sg, gr] = await Promise.all([facesStatus(), faceSuggestions(), faceGroups()]);
      setStatus(st);
      setSuggest(sg);
      setGroups(gr);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);
  useReloadOn((e) => e.type === PEOPLE_CHANGED, () => void load());

  // Elke 10 s zolang er werk is en de pagina te zien is; terug in beeld meteen
  useEffect(() => {
    if (!status || !busy(status)) return;
    const tick = () => {
      if (document.visibilityState === "visible") void load();
    };
    const timer = setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [status, load]);

  function close(key: string) {
    setOpen(null);
    requestAnimationFrame(() => buttons.current.get(key)?.focus());
    void load().then(() => setClosed((n) => n + 1));
  }

  // Na het laatste voorstel is de kaart na het opnieuw laden weg, en de focus met hem: dan de
  // eerste kaart die er nog is, anders "Niet benoemde gezichten wissen", anders de regel
  useEffect(() => {
    if (!closed || document.activeElement !== document.body) return;
    ([...buttons.current.values()].find(Boolean) ?? wipeButton.current ?? statusRef.current)?.focus();
  }, [closed]);

  async function wipe(n: number) {
    const ok = await confirm({
      title: t("photos_faces.wipe.button"),
      message: t("photos_faces.wipe.text", { n }),
      confirm: t("photos_faces.wipe.confirm"),
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await wipeUnnamedFaces();
      toast(t("photos_faces.wipe.queued", { n: r.queued }), { kind: "ok" });
      void load();
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  const ref = (key: string) => (el: HTMLButtonElement | null) => {
    buttons.current.set(key, el);
  };

  return (
    <div className="people-faces">
      {error && <p role="alert">{error}</p>}
      {status && (
        <p className="faces-status" role="status" data-faces="status" tabIndex={-1} ref={statusRef}>
          {statusLine(status)}
        </p>
      )}
      {(open?.kind === "person" || !!suggest?.people.length) && (
        <section aria-labelledby="faces-voorstellen">
          <h2 id="faces-voorstellen">{t("photos_faces.suggest.title")}</h2>
          {open?.kind === "person" ? (
            <FaceGrid
              title={t("photos_faces.suggest.question", { name: open.name })}
              mode="suggest"
              person={open}
              load={(c) => personFaceSuggestions(open.id, c, FACES_PAGE)}
              onClose={() => close(`p:${open.id}`)}
              onChanged={() => void load()}
            />
          ) : (
            <ul className="faces-cards">
              {suggest?.people.map((ps) => (
                <li key={ps.person.id}>
                  <FaceCard
                    faces={ps.sample}
                    label={t("photos_faces.suggest.question", { name: ps.person.name })}
                    count={t("photos_faces.suggest.count", { n: ps.count })}
                    open={false}
                    onOpen={() => setOpen({ kind: "person", id: ps.person.id, name: ps.person.name })}
                    button={ref(`p:${ps.person.id}`)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {(open?.kind === "group" || !!groups?.groups.length) && (
        <section aria-labelledby="faces-groepen">
          <h2 id="faces-groepen">{t("photos_faces.group.title")}</h2>
          {open?.kind === "group" ? (
            <FaceGrid
              title={t("photos_faces.group.title")}
              mode="group"
              load={(c) => faceGroup(open.key, c)}
              onClose={() => close(`g:${open.key}`)}
              onChanged={() => void load()}
            />
          ) : (
            <ul className="faces-cards">
              {groups?.groups.map((g) => (
                <li key={g.key}>
                  <FaceCard
                    faces={g.faces}
                    label={t("photos_faces.group.title")}
                    count={t("photos_faces.group.count", { n: g.count })}
                    open={false}
                    onOpen={() => setOpen({ kind: "group", key: g.key })}
                    button={ref(`g:${g.key}`)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {status && status.faces.open > 0 && !status.wiping && (
        <p>
          <Button ref={wipeButton} variant="danger" onClick={() => void wipe(status.faces.open)}>
            {t("photos_faces.wipe.button")}
          </Button>
        </p>
      )}
    </div>
  );
}
