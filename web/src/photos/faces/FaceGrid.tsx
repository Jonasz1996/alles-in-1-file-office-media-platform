// Het raster van gezichten (fase 6, X2b; Keuze 66): alle voorstellen voor één persoon ("Is dit
// Anna?") of alle gezichten van één groep ("Wie is dit?"). Een raster met roving tabindex; de
// toetsen (scope faces, zie logic.gridKey):
//   pijlen, Home, End  naar een ander gezicht
//   J/Y, N, V/H        ja, nee, niet tonen (bij voorstellen; V/H ook in een groep); de focus gaat
//                      daarna naar het volgende gezicht zonder keuze
//   Spatie             selecteren of niet (in "Wie is dit?"; eerst is alles geselecteerd)
//   Enter              de foto openen in de viewer
//   Ctrl+Z             de laatste keuze ongedaan maken, zolang de melding staat
//   Escape             het raster sluiten (onClose zet de focus terug op de kaart)
// Bij het openen staat de focus op het eerste gezicht (of op Sluiten als er niets is).
// Elke keuze zegt via een melding (aria-live) wat er gebeurde. In "Wie is dit?" gaan alleen de
// gezichten die je zag (in beeld waren) en die geselecteerd zijn naar confirm, nooit de groep.

import { type JSX, type KeyboardEvent, Suspense, useEffect, useId, useRef, useState } from "react";
import { api, type FileNode } from "../../api";
import { errorText, lazyWithAreas, t } from "../../i18n";
import { seg } from "../../search/wire";
import { Button } from "../../ui/Button";
import { moveIndex, useScope } from "../../ui/keys";
import { announce, toast } from "../../ui/toast";
import type { ViewerProps } from "../../viewer/types";
import type { ComboOption } from "../people/combo";
import type { Person } from "../people/types";
import { confirmFaces, hideFaces, rejectFaces, resetFaces } from "./api";
import { Crop } from "./Crop";
import { confirmIds, type GridMode, gridKey, inChunks, LastChoice, nextOpen, PersonCombo, peopleApi } from "./logic";
import type { FaceOut, FacePage } from "./types";

type Verdict = "yes" | "no" | "hide";

// De viewer pas als hij nodig is (Enter op een gezicht): zo blijft hij buiten de chunks van
// gezichten (budget faces, 12 kB)
const Viewer = lazyWithAreas<ViewerProps>(() => import("../../viewer/Viewer"), ["viewer"]);

export interface FaceGridProps {
  /** De kop van het raster ("Is dit Anna?", "Wie is dit?") */
  title: string;
  mode: GridMode;
  /** Bij voorstellen: wie er wordt voorgesteld */
  person?: { id: string; name: string };
  load(cursor: string | null): Promise<FacePage>;
  onClose(): void;
  /** Na een keuze (de ouder ververst zijn kaarten) */
  onChanged?(): void;
}

export function FaceGrid(p: FaceGridProps): JSX.Element {
  const id = useId();
  const [faces, setFaces] = useState<FaceOut[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [seen, setSeen] = useState<ReadonlySet<string>>(new Set());
  const [index, setIndex] = useState(0);
  const [viewing, setViewing] = useState<FileNode | null>(null);
  const [mine, setMine] = useState<Person[]>([]);
  const [pick, setPick] = useState<ComboOption | null>(null);
  const cells = useRef<(HTMLDivElement | null)[]>([]);
  const closeButton = useRef<HTMLButtonElement | null>(null);
  const first = useRef(true);
  const last = useRef(new LastChoice());
  useScope("faces");

  async function more(from: string | null) {
    setLoading(true);
    try {
      const page = await p.load(from);
      setFaces((old) => (from ? [...old, ...page.faces] : page.faces));
      setSelected((old) => new Set([...old, ...page.faces.map((f) => f.id)]));
      setCursor(page.cursor);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setLoading(false);
    }
  }

  // Eén keer bij het openen
  useEffect(() => {
    void more(null);
    if (p.mode === "group") {
      peopleApi()
        .then((m) => m.people())
        .then((r) => setMine(r.people), () => {});
    }
  }, []);

  // Na de eerste pagina staat de focus in het raster (de knop van de kaart is dan weg); zonder
  // gezichten op Sluiten
  useEffect(() => {
    if (!first.current || loading) return;
    first.current = false;
    if (faces.length > 0) cells.current[0]?.focus();
    else closeButton.current?.focus();
  }, [loading, faces]);

  // Gezien: een cel die in beeld kwam. Zonder IntersectionObserver telt elke getoonde cel.
  useEffect(() => {
    const els = cells.current.filter((el): el is HTMLDivElement => !!el);
    const mark = (ids: string[]) => setSeen((old) => (ids.every((x) => old.has(x)) ? old : new Set([...old, ...ids])));
    if (typeof IntersectionObserver === "undefined") {
      mark(els.map((el) => el.dataset.face ?? ""));
      return;
    }
    const watch = new IntersectionObserver(
      (entries) => mark(entries.filter((e) => e.isIntersecting).map((e) => (e.target as HTMLElement).dataset.face ?? "")),
      { threshold: 0.5 },
    );
    for (const el of els) watch.observe(el);
    return () => watch.disconnect();
  }, [faces]);

  const focus = (i: number) => {
    setIndex(i);
    requestAnimationFrame(() => cells.current[i]?.focus());
  };

  /** Kolommen: de cellen op de hoogte van de eerste. */
  const columns = () => {
    const top = cells.current[0]?.offsetTop;
    return Math.max(1, cells.current.filter((el) => el && el.offsetTop === top).length);
  };

  function remember(ids: string[], text: string, undo: () => void) {
    const run = async () => {
      try {
        await resetFaces(ids);
        undo();
        announce(t("photos_faces.done.undone"));
        p.onChanged?.();
      } catch (e) {
        toast(errorText(e), { kind: "error" });
      }
    };
    const close = toast(text, {
      kind: "ok",
      action: {
        label: t("photos_faces.done.undo"),
        run: () => {
          last.current.clear();
          void run();
        },
      },
    });
    last.current.set(() => {
      close();
      void run();
    });
  }

  async function choose(i: number, v: Verdict) {
    const f = faces[i];
    if (!f || verdicts[f.id]) return;
    const who = p.person;
    try {
      if (v === "hide") await hideFaces([f.id]);
      else if (!who) return;
      else if (v === "yes") await confirmFaces({ faces: [f.id], person_id: who.id });
      else await rejectFaces({ faces: [f.id], person_id: who.id });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
      return;
    }
    setVerdicts((old) => ({ ...old, [f.id]: v }));
    const name = who?.name ?? "";
    const text =
      v === "yes"
        ? t("photos_faces.done.yes", { n: 1, name })
        : v === "no"
          ? t("photos_faces.done.no", { name })
          : t("photos_faces.done.hidden");
    remember([f.id], text, () => {
      setVerdicts(({ [f.id]: _, ...rest }) => rest);
      focus(i);
    });
    p.onChanged?.();
    const next = nextOpen(faces.length, i, (j) => j === i || !!verdicts[faces[j].id]);
    if (next >= 0) focus(next);
  }

  async function confirmGroup() {
    if (!pick) {
      toast(t("photos_faces.group.pick"), { kind: "error" });
      return;
    }
    const open = faces.filter((f) => !verdicts[f.id]);
    const ids = confirmIds(open, selected, seen);
    if (ids.length === 0) return;
    const target = pick.kind === "person" ? { person_id: pick.person.id } : { name: pick.name };
    const name = pick.kind === "person" ? pick.person.name : pick.name;
    try {
      for (const chunk of inChunks(ids)) await confirmFaces({ faces: chunk, ...target });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
      return;
    }
    setVerdicts((old) => ({ ...old, ...Object.fromEntries(ids.map((x) => [x, "yes" as const])) }));
    remember(ids, t("photos_faces.done.yes", { n: ids.length, name }), () =>
      setVerdicts((old) => Object.fromEntries(Object.entries(old).filter(([x]) => !ids.includes(x)))),
    );
    p.onChanged?.();
  }

  async function open(i: number) {
    const f = faces[i];
    if (!f) return;
    try {
      setViewing(await api.request<FileNode>("GET", `/api/v1/files/${seg(f.node_id)}`));
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const action = gridKey(e, p.mode);
    if (!action) return;
    e.preventDefault();
    e.stopPropagation();
    const f = faces[index];
    switch (action) {
      case "close":
        p.onClose();
        return;
      case "undo":
        last.current.take()?.();
        return;
      case "open":
        void open(index);
        return;
      case "select":
        if (!f || verdicts[f.id]) return;
        setSelected((old) => {
          const s = new Set(old);
          if (!s.delete(f.id)) s.add(f.id);
          return s;
        });
        return;
      case "yes":
      case "no":
      case "hide":
        void choose(index, action);
        return;
      default: {
        const next = moveIndex(index, action, faces.length, columns());
        if (next >= 0) focus(next);
      }
    }
  }

  const left = faces.filter((f) => !verdicts[f.id]);
  const chosen = confirmIds(left, selected, seen).length;
  const label = (v: Verdict) =>
    t(v === "yes" ? "photos_faces.grid.chosenYes" : v === "no" ? "photos_faces.grid.chosenNo" : "photos_faces.grid.chosenHidden");

  return (
    <section className="faces-grid" aria-labelledby={`${id}-kop`}>
      <h3 id={`${id}-kop`}>{p.title}</h3>
      <p className="muted" id={`${id}-hulp`}>
        {t("photos_faces.grid.help")}{" "}
        {t(p.mode === "group" ? "photos_faces.grid.helpGroup" : "photos_faces.grid.helpSuggest")}
      </p>
      {!loading && faces.length === 0 && <p className="muted">{t("photos_faces.grid.empty")}</p>}
      <div
        role="grid"
        aria-labelledby={`${id}-kop`}
        aria-describedby={`${id}-hulp`}
        aria-multiselectable={p.mode === "group" || undefined}
        className="faces-cells"
        onKeyDown={onKeyDown}
      >
        <div role="row">
          {faces.map((f, i) => {
            const v = verdicts[f.id];
            return (
              <div
                key={f.id}
                ref={(el) => {
                  cells.current[i] = el;
                }}
                role="gridcell"
                data-face={f.id}
                tabIndex={i === index ? 0 : -1}
                aria-selected={p.mode === "group" ? !v && selected.has(f.id) : undefined}
                aria-label={`${t("photos_faces.grid.face", { i: i + 1, total: faces.length })}${v ? `: ${label(v)}` : ""}`}
                className={`face-cell${v ? ` done ${v}` : ""}`}
                onFocus={() => setIndex(i)}
                onDoubleClick={() => void open(i)}
                onClick={() => focus(i)}
              >
                <Crop id={f.id} size={192} />
                {v ? (
                  <span className="face-verdict">{label(v)}</span>
                ) : p.mode === "suggest" ? (
                  <span className="face-acts">
                    <button type="button" tabIndex={-1} onClick={() => void choose(i, "yes")}>
                      {t("photos_faces.suggest.yes")}
                    </button>
                    <button type="button" tabIndex={-1} onClick={() => void choose(i, "no")}>
                      {t("photos_faces.suggest.no")}
                    </button>
                    <button type="button" tabIndex={-1} onClick={() => void choose(i, "hide")}>
                      {t("photos_faces.suggest.hide")}
                    </button>
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
      {cursor && (
        <p>
          <Button busy={loading} onClick={() => void more(cursor)}>
            {t("photos_faces.grid.more")}
          </Button>
        </p>
      )}
      {p.mode === "group" && left.length > 0 && (
        <form
          className="faces-name"
          onSubmit={(e) => {
            e.preventDefault();
            void confirmGroup();
          }}
        >
          <p role="status">{t("photos_faces.group.selected", { n: chosen, total: left.length })}</p>
          <Suspense fallback={null}>
            <PersonCombo
              label={t("photos_faces.group.name")}
              people={mine}
              allowNew
              clearOnPick={false}
              onPick={setPick}
              onText={() => setPick(null)}
              onEscape={p.onClose}
            />
          </Suspense>
          <Button type="submit" variant="primary" disabled={chosen === 0}>
            {t("photos_faces.group.confirm")}
          </Button>
        </form>
      )}
      <p>
        <Button ref={closeButton} onClick={p.onClose}>
          {t("photos_faces.grid.close")}
        </Button>
      </p>
      {viewing && (
        <Suspense fallback={null}>
          <Viewer
            items={[viewing]}
            index={0}
            onIndexChange={() => {}}
            onClose={() => {
              setViewing(null);
              focus(index);
            }}
          />
        </Suspense>
      )}
    </section>
  );
}

/** Een kaart met zes uitsneden en de knop die het raster opent ("Is dit Anna?", "Wie is dit?").
 * De knop houdt zijn ref, zodat Escape in het raster de focus hier terugzet. */
export function FaceCard({
  faces,
  label,
  count,
  open,
  onOpen,
  button,
}: {
  faces: readonly FaceOut[];
  label: string;
  count?: string;
  open: boolean;
  onOpen(): void;
  button(el: HTMLButtonElement | null): void;
}): JSX.Element {
  const id = useId();
  return (
    <div className="faces-card">
      <span className="faces-sample" aria-hidden="true">
        {faces.slice(0, 6).map((f) => (
          <Crop key={f.id} id={f.id} size={96} />
        ))}
      </span>
      <button
        ref={button}
        type="button"
        className="btn"
        aria-expanded={open}
        aria-describedby={count ? `${id}-n` : undefined}
        onClick={onOpen}
      >
        {label}
      </button>
      {count && (
        <span id={`${id}-n`} className="muted">
          {count}
        </span>
      )}
    </div>
  );
}
