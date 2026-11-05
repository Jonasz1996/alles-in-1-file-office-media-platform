// /fotos/personen/:id (fase 6, van Z6; Keuze 38): de foto's van één eigen persoon, in hetzelfde
// raster als een album (met de viewer en downloaden), en wat je met de persoon doet: hernoemen,
// samenvoegen met een ander, verbergen, wissen, en "Niet in herinneringen" (Keuze 41). Het
// contract: de default export is de pagina van de route (RouteProps, met params.id), in een eigen
// chunk. Er is geen GET /people/{id}: de pagina zoekt de persoon in GET /people (ook de verborgen).
//
// Z0b (review contract F3): de plek van X2, PersonFaces ("Misschien ook Anna"), lazy met het
// gebied photos_faces. Z6 houdt hem; src/lazySlots.test.ts eist hem.

import { type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useReloadOn } from "../../events";
import { errorText, lazyWithAreas, t, useAreas } from "../../i18n";
import { Link } from "../../links";
import { navigate, type RouteProps } from "../../router";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { confirm, prompt } from "../../ui/dialogs";
import { EmptyState } from "../../ui/EmptyState";
import { useScope, useShortcuts } from "../../ui/keys";
import { LazySlot } from "../../ui/LazySlot";
import { PageTitle } from "../../ui/PageTitle";
import { Skeleton } from "../../ui/Spinner";
import { toast } from "../../ui/toast";
import { useColumns } from "../../ui/virtual";
import { downloadZip, SelectionBar, usePhotoBrowser, useStoreVersion } from "../browse";
import type { PersonFacesProps } from "../faces/PersonFaces";
import { GAP, type GridControl, MIN_CELL, PhotoGrid } from "../Grid";
import { useThumbPatches } from "../live";
import type { Placed } from "../range";
import { SectionStore } from "../store";
import { buildLayout } from "../timeline";
import {
  deletePerson,
  hideMemories,
  memoryHides,
  mergePerson,
  people,
  personPhotos,
  unhideMemories,
  updatePerson,
} from "./api";
import type { ComboOption } from "./combo";
import { pagedIds } from "./filters";
import { PersonCombo } from "./PersonCombo";
import { PEOPLE_CHANGED, type Person } from "./types";
import "../photos.css";
import "./people.css";

const PersonFaces = lazyWithAreas<PersonFacesProps>(() => import("../faces/PersonFaces"), ["photos_faces"]);

/** Een pagina van /people/{id}/photos zoals het raster hem leest. */
async function page(id: string, after: string | null) {
  const r = await personPhotos(id, after);
  return { items: r.items, next: r.cursor };
}

export default function PersonPage({ params }: RouteProps): JSX.Element {
  useScope("person");
  const id = params.id;
  // Het raster en de selectie gebruiken de teksten van Foto's
  const ready = useAreas(["photos"]);
  const [person, setPerson] = useState<Person | null>(null);
  const [all, setAll] = useState<readonly Person[]>([]);
  const [noMemories, setNoMemories] = useState(false);
  const [gone, setGone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [count, setCount] = useState(0);
  const [merging, setMerging] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const control = useRef<GridControl | null>(null);
  const { columns, cellWidth } = useColumns(wrap, MIN_CELL, GAP);
  const rowHeight = Math.max(MIN_CELL / 2, Math.round(cellWidth)) + GAP;

  const store = useMemo(() => new SectionStore((_s, after) => page(id, after)), [id]);
  const version = useStoreVersion(store);

  const load = useCallback(async () => {
    try {
      const [shown, hidden, hides] = await Promise.all([people(false), people(true), memoryHides()]);
      const everyone = [...shown.people, ...hidden.people];
      const p = everyone.find((x) => x.id === id) ?? null;
      setAll(everyone);
      setPerson(p);
      setGone(p === null);
      setNoMemories(hides.hides.some((h) => h.kind === "person" && h.ref === id));
      if (p) {
        setCount(p.count);
        store.setSections([p.count], undefined, () => 0);
      }
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [id, store]);

  useEffect(() => {
    store.onMismatch = (_s, got) => {
      setCount(got);
      store.setSections([got], undefined, () => 0);
    };
    store.onError = (e) => setError(errorText(e));
    void load();
  }, [store, load]);
  useReloadOn(
    (e) => e.type === PEOPLE_CHANGED || e.type === "connected",
    () => void load(),
  );
  useThumbPatches(store);

  const layout = useMemo(() => buildLayout([count], columns, rowHeight, 0, false), [count, columns, rowHeight]);
  const section = useMemo(() => (version >= 0 ? (s: number) => store.section(s) : () => undefined), [store, version]);
  const onVisible = useCallback((sections: number[]) => store.want(sections), [store]);
  // Een reeks over wat nog niet geladen is: bladeren door /people/{id}/photos (zelfde volgorde)
  const fetchIds = useCallback((a: Placed, b: Placed) => pagedIds((after) => page(id, after), a, b), [id]);
  const b = usePhotoBrowser({ store, layout, fetchIds, control, scope: "person" });
  const ids = useMemo(() => [...b.selection.ids], [b.selection.ids]);

  async function rename() {
    if (!person) return;
    const name = await prompt({
      title: t("photos_people.personPage.rename"),
      label: t("photos_people.personPage.name"),
      initial: person.name,
      confirm: t("common.save"),
      validate: (v) => (v.trim() ? null : t("photos_people.row.nameHint")),
    });
    if (name === null || name.trim() === person.name) return;
    try {
      setPerson(await updatePerson(person.id, { name: name.trim() }));
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function toggleHidden() {
    if (!person) return;
    try {
      setPerson(await updatePerson(person.id, { hidden: !person.hidden }));
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function toggleMemories() {
    if (!person) return;
    try {
      if (noMemories) {
        await unhideMemories("person", person.id);
        toast(t("photos_people.personPage.memoriesOn", { name: person.name }), { kind: "ok" });
      } else {
        await hideMemories({ kind: "person", ref: person.id });
        toast(t("photos_people.personPage.memoriesOff", { name: person.name }), { kind: "ok" });
      }
      setNoMemories(!noMemories);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function remove() {
    if (!person) return;
    const ok = await confirm({
      title: t("photos_people.personPage.delete"),
      message: t("photos_people.personPage.deleteText", { name: person.name }),
      confirm: t("photos_people.personPage.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await deletePerson(person.id);
      toast(t("photos_people.personPage.deleted", { name: person.name }), { kind: "ok" });
      navigate("/fotos/personen");
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  const mine = () => !!person;
  useShortcuts(
    [
      { combo: "f2", label: t("photos_people.personPage.rename"), run: () => void rename(), when: mine },
      { combo: "shift+delete", label: t("photos_people.personPage.delete"), run: () => void remove(), when: mine },
    ],
    { scope: "person" },
  );

  if (gone) {
    return (
      <main className="wide">
        <PageTitle>{t("photos_people.person")}</PageTitle>
        <EmptyState icon="user" title={t("photos_people.personPage.gone")}>
          <p>
            <Link to="/fotos/personen">{t("photos_people.personPage.back")}</Link>
          </p>
        </EmptyState>
      </main>
    );
  }

  const others = all.filter((p) => p.id !== id);
  return (
    <main className="wide person-page">
      <PageTitle
        actions={
          person && (
            <>
              <Button icon="rename" onClick={() => void rename()}>
                {t("photos_people.personPage.rename")}
              </Button>
              <Button icon="move" onClick={() => setMerging(true)}>
                {t("photos_people.personPage.merge")}
              </Button>
              <Button onClick={() => void toggleHidden()}>
                {person.hidden ? t("photos_people.personPage.unhide") : t("photos_people.personPage.hide")}
              </Button>
              <Button onClick={() => void toggleMemories()}>
                {noMemories ? t("photos_people.personPage.memoriesBack") : t("photos_people.personPage.memories")}
              </Button>
              <Button icon="trash" variant="danger" onClick={() => void remove()}>
                {t("photos_people.personPage.delete")}
              </Button>
            </>
          )
        }
      >
        {person?.name ?? t("photos_people.person")}
      </PageTitle>
      <p className="muted album-meta">
        <Link to="/fotos/personen">{t("photos_people.personPage.back")}</Link>
        {person && ` · ${t("photos_people.count", { n: count })}`}
      </p>
      {person?.hidden && <p className="muted">{t("photos_people.personPage.hiddenNote")}</p>}
      <LazySlot>
        <PersonFaces personId={params.id} />
      </LazySlot>
      {error && <p role="alert">{error}</p>}
      <div ref={wrap} className="photos-wrap">
        {(!person || !ready) && !error && <Skeleton />}
        {person && ready && count === 0 && (
          <EmptyState icon="user" title={t("photos_people.personPage.empty", { name: person.name })} />
        )}
        {person && ready && count > 0 && (
          <PhotoGrid
            layout={layout}
            rowHeight={rowHeight}
            headerHeight={0}
            section={section}
            selection={b.selection}
            focus={b.focus}
            setFocus={b.setFocus}
            label={t("photos_people.personPage.grid", { name: person.name })}
            onVisible={onVisible}
            onCellClick={b.onCellClick}
            onShiftMove={b.onShiftMove}
            control={control}
          />
        )}
      </div>
      {ready && (
        <SelectionBar count={b.selection.ids.size} onClear={b.clear} onGone={b.focusGrid}>
          <Button icon="download" onClick={() => void downloadZip(ids, person?.name ?? t("photos_people.person"))}>
            {t("photos.download")}
          </Button>
        </SelectionBar>
      )}
      {merging && person && (
        <MergeDialog
          person={person}
          others={others}
          onClose={() => setMerging(false)}
          onMerged={(into) => {
            setMerging(false);
            toast(t("photos_people.personPage.merged", { name: person.name, into: into.name }), { kind: "ok" });
            navigate(`/fotos/personen/${encodeURIComponent(into.id)}`);
          }}
        />
      )}
      {ready && b.viewer}
    </main>
  );
}

function MergeDialog({
  person,
  others,
  onClose,
  onMerged,
}: {
  person: Person;
  others: readonly Person[];
  onClose(): void;
  onMerged(into: Person): void;
}): JSX.Element {
  const [into, setInto] = useState<Person | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function merge() {
    if (!into || busy) return;
    setBusy(true);
    try {
      onMerged(await mergePerson(person.id, into.id));
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={t("photos_people.personPage.mergeTitle", { name: person.name })}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={busy} disabled={!into} onClick={() => void merge()}>
            {t("photos_people.personPage.merge")}
          </Button>
        </>
      }
    >
      {others.length === 0 ? (
        <p>{t("photos_people.personPage.mergeNone")}</p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void merge();
          }}
        >
          <p className="dialog-message">{t("photos_people.personPage.mergeText", { name: person.name })}</p>
          <PersonCombo
            label={t("photos_people.personPage.mergeInto")}
            people={others}
            clearOnPick={false}
            autoFocus
            onPick={(o: ComboOption) => setInto(o.kind === "person" ? o.person : null)}
            onText={() => setInto(null)}
          />
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </Dialog>
  );
}
