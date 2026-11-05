// De rij Personen in het infopaneel (fase 6, van Z6; Keuze 37): wie er op de foto staat, en
// "Persoon toevoegen" met een kader, alleen voor de eigenaar (GET /files/{id}/people; voor een
// ander []). Of je de eigenaar bent, zegt GET /files/{id} (permission); voor een ander toont de
// rij niets. Het contract: de default export met InfoRowProps, een eigen chunk, met het gebied
// photos_people.
//
// Toetsenbord: Persoon toevoegen opent een formulier met de naam (een combobox: ↓ en ↑ door je
// personen, Enter kiest, nog een Enter voegt toe), "Kader tekenen" en het kader zelf (de pijlen
// verplaatsen, Shift met een pijl maakt groter of kleiner). Esc sluit het formulier en zet de
// focus terug op Persoon toevoegen; de viewer gaat er niet van dicht.

import { type FormEvent, type JSX, type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type FileNode } from "../api";
import { useServerEvents } from "../events";
import { errorText, fold, t } from "../i18n";
import { Link } from "../links";
import { addFilePerson, filePeople, people as ownPeople, removeFilePerson } from "../photos/people/api";
import { BoxEditor } from "../photos/people/BoxEditor";
import { START_BOX } from "../photos/people/box";
import type { ComboOption } from "../photos/people/combo";
import { PersonCombo } from "../photos/people/PersonCombo";
import { type AddPerson, type Box, type FilePerson, PEOPLE_CHANGED, type Person } from "../photos/people/types";
import { seg } from "../search/wire";
import { permissionOf } from "../sharing";
import { Button, IconButton } from "../ui/Button";
import { announce, toast } from "../ui/toast";
import type { InfoRowProps } from "./infoRows";
import "../photos/people/people.css";

/** Wat er op de foto staat, en of hij van jou is. */
type Loaded = { owner: boolean; people: FilePerson[] };

export default function PeopleRow({ item, detail, kind }: InfoRowProps): JSX.Element | null {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [adding, setAdding] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  const fileId = item.id;

  const load = useCallback(async () => {
    try {
      const [node, on] = await Promise.all([
        api.request<FileNode>("GET", `/api/v1/files/${seg(fileId)}`),
        filePeople(fileId),
      ]);
      setLoaded({ owner: permissionOf(node) === "owner", people: on.people });
    } catch {
      // Weg, in de prullenbak of geen verbinding: dan geen rij (het paneel blijft werken)
      setLoaded(null);
    }
  }, [fileId]);

  useEffect(() => {
    void load();
  }, [load]);

  useServerEvents((e) => {
    if (e.type === PEOPLE_CHANGED) void load();
  });

  if (!loaded?.owner) return null;

  async function remove(p: FilePerson) {
    try {
      await removeFilePerson(fileId, p.person.id);
      announce(t("photos_people.row.removed", { name: p.person.name }));
      await load();
      addButton.current?.focus();
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  function close() {
    setAdding(false);
    // Na het renderen bestaat de knop weer
    requestAnimationFrame(() => addButton.current?.focus());
  }

  const headingId = `personen-${fileId}`;
  return (
    <section className="people-row" aria-labelledby={headingId}>
      <h4 id={headingId}>{t("photos_people.row.title")}</h4>
      {loaded.people.length === 0 ? (
        <p className="muted">{t("photos_people.row.none")}</p>
      ) : (
        <ul className="people-on">
          {loaded.people.map((p) => (
            <li key={p.person.id}>
              <Link
                to={`/fotos/personen/${encodeURIComponent(p.person.id)}`}
                title={t("photos_people.row.open", { name: p.person.name })}
              >
                {p.person.name}
              </Link>
              {p.box && <span className="muted">{t("photos_people.row.withFrame")}</span>}
              <IconButton
                icon="close"
                className="ghost"
                label={t("photos_people.row.remove", { name: p.person.name })}
                onClick={() => void remove(p)}
              />
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <AddForm
          fileId={fileId}
          src={kind === "image" ? (detail?.thumb ?? detail?.large ?? null) : null}
          canBox={kind === "image"}
          on={loaded.people}
          onDone={async (name) => {
            toast(t("photos_people.row.added", { name }), { kind: "ok" });
            await load();
            close();
          }}
          onCancel={close}
        />
      ) : (
        <Button ref={addButton} icon="user" onClick={() => setAdding(true)}>
          {t("photos_people.row.add")}
        </Button>
      )}
    </section>
  );
}

function AddForm({
  fileId,
  src,
  canBox,
  on,
  onDone,
  onCancel,
}: {
  fileId: string;
  src: string | null;
  canBox: boolean;
  on: readonly FilePerson[];
  onDone(name: string): Promise<void>;
  onCancel(): void;
}): JSX.Element {
  const [mine, setMine] = useState<readonly Person[]>([]);
  const [chosen, setChosen] = useState<ComboOption | null>(null);
  const [typed, setTyped] = useState("");
  const [box, setBox] = useState<Box | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const exclude = useMemo(() => new Set(on.map((p) => p.person.id)), [on]);

  useEffect(() => {
    let live = true;
    ownPeople(false).then(
      (r) => {
        if (live) setMine(r.people);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, []);

  /** Wat er toegevoegd wordt: de keuze uit de lijst, anders wat er getypt is (een eigen persoon
   * die precies zo heet, of een nieuwe naam). */
  function target(): { body: AddPerson; name: string } | null {
    if (chosen?.kind === "person") return { body: { person_id: chosen.person.id }, name: chosen.person.name };
    const name = (chosen?.kind === "new" ? chosen.name : typed).trim().replace(/\s+/g, " ");
    if (!name) return null;
    const same = mine.find((p) => fold(p.name) === fold(name));
    return same ? { body: { person_id: same.id }, name: same.name } : { body: { name }, name };
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const what = target();
    if (!what) {
      setError(t("photos_people.row.nameHint"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addFilePerson(fileId, box ? { ...what.body, box } : what.body);
      await onDone(what.name);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLFormElement>) {
    // De combobox sluit eerst zijn lijst (en zet dan defaultPrevented); daarna sluit Esc het formulier
    if (e.key !== "Escape" || e.defaultPrevented) return;
    e.preventDefault();
    e.stopPropagation();
    onCancel();
  }

  return (
    <form className="people-add" onSubmit={(e) => void submit(e)} onKeyDown={onKeyDown}>
      <PersonCombo
        label={t("photos_people.row.name")}
        hint={t("photos_people.row.nameHint")}
        people={mine}
        exclude={exclude}
        allowNew
        clearOnPick={false}
        autoFocus
        onPick={(o) => setChosen(o)}
        onText={(text) => {
          setChosen(null);
          setTyped(text);
        }}
      />
      {canBox && (
        <label className="check">
          <input type="checkbox" checked={box !== null} onChange={(e) => setBox(e.target.checked ? START_BOX : null)} />
          {t("photos_people.row.withBox")}
        </label>
      )}
      {canBox && box && <BoxEditor src={src} box={box} onChange={setBox} />}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <div className="people-add-actions">
        <Button type="submit" variant="primary" busy={busy}>
          {t("photos_people.row.save")}
        </Button>
        <Button onClick={onCancel}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}
