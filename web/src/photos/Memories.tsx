// "Vandaag, N jaar geleden" bovenaan /fotos (fase 6, van Z6; Keuze 41): per eerder jaar je foto's
// van deze dag (GET /photos/memories; de dag is die van de server). Een eigen chunk met het gebied
// photos_people; /fotos laadt hem alleen zonder filters. Niets te tonen (of herinneringen uit, of
// een fout): dan staat er niets.
//
// Toetsenbord: elke foto is een knop (Tab), Enter opent hem in de viewer (← en → door de foto's
// van dat jaar); "Deze dag verbergen" (met Ongedaan maken in de melding) zet de focus daarna op de
// kop van de pagina.
//
// 0.8.2 (z6.md Open): "Een map verbergen…" naast de kop van het blok opent de mappenkiezer van
// Bestanden (FolderPicker, lazy met het gebied browser). Alleen een eigen map, niet Mijn bestanden
// zelf (folderProblem); de API verbergt de map met alles eronder (memory_hide, kind folder). Net
// als bij een dag: Ongedaan maken in de melding, en daarna de focus op de kop van de pagina.
//
// /fotos vraagt het antwoord al tegelijk met de maanden (initial) en toont het blok in dezelfde
// commit als de tijdlijn: zo schuift die niet omlaag (review golf 2, web F).
//
// Het blok staat boven de tijdlijn en mag die niet trager maken (de scroll-meting van e2e/perf):
// de thumbnails komen via de wachtrij van het raster (een paar per frame, niets tijdens snel
// scrollen), en een jaar buiten beeld kost niets (content-visibility in people.css).

import { type JSX, Suspense, useCallback, useEffect, useId, useState } from "react";
import type { FileNode } from "../api";
import type { FolderPicker as Picker } from "../FolderPicker";
import { errorText, lazyWithAreas, type MessageKey, t } from "../i18n";
import { permissionOf } from "../sharing";
import { Button } from "../ui/Button";
import { focusPage } from "../ui/PageTitle";
import { toast } from "../ui/toast";
import { Viewer } from "../viewer";
import { type PhotoItem, wallLabel } from "./api";
import { requestImage } from "./Grid";
import { hideMemories, memories, unhideMemories } from "./people/api";
import type { MemoryYear } from "./people/types";
import "./people/people.css";

const FolderPicker = lazyWithAreas<Parameters<typeof Picker>[0]>(
  () => import("../FolderPicker").then((m) => ({ default: m.FolderPicker })),
  ["browser"],
);

/** Waarom deze map niet uit je herinneringen kan, of null als het mag: alleen een eigen map, en
 * niet Mijn bestanden zelf (dat is herinneringen uitzetten, onder Account). */
export function folderProblem(folder: Pick<FileNode, "parent_id" | "permission">): MessageKey | null {
  if (permissionOf(folder) !== "owner") return "photos_people.memories.folderNotOwn";
  if (folder.parent_id === null) return "photos_people.memories.folderRoot";
  return null;
}

export interface MemoriesProps {
  /** Het antwoord dat /fotos al had (tegelijk met de maanden gevraagd); null: zelf vragen */
  initial?: MemoryYear[] | null;
  /** Met filters aan: niet tonen, maar wel blijven (geen nieuwe vraag als de filters weg zijn) */
  hidden?: boolean;
}

export default function Memories({ initial = null, hidden = false }: MemoriesProps): JSX.Element | null {
  const id = useId();
  const [years, setYears] = useState<MemoryYear[]>(initial ?? []);
  const [open, setOpen] = useState<{ year: number; index: number } | null>(null);
  const [picking, setPicking] = useState(false);

  const load = useCallback(async () => {
    try {
      setYears((await memories()).years);
    } catch {
      setYears([]); // herinneringen zijn een extraatje: zonder blijft /fotos gewoon werken
    }
  }, []);

  const asked = initial !== null;
  useEffect(() => {
    if (!asked) void load();
  }, [asked, load]);

  async function hide(y: MemoryYear) {
    try {
      await hideMemories({ kind: "day", ref: y.day });
      setYears((list) => list.filter((x) => x.day !== y.day));
      requestAnimationFrame(() => focusPage());
      toast(t("photos_people.memories.hidden"), {
        action: {
          label: t("photos_people.memories.undo"),
          run: () => {
            unhideMemories("day", y.day).then(load, (e) => toast(errorText(e), { kind: "error" }));
          },
        },
      });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function hideFolder(id: string, folder: FileNode) {
    setPicking(false);
    try {
      await hideMemories({ kind: "folder", ref: id });
      await load();
      requestAnimationFrame(() => focusPage());
      toast(t("photos_people.memories.folderHidden", { name: folder.name }), {
        action: {
          label: t("photos_people.memories.undo"),
          run: () => {
            unhideMemories("folder", id).then(load, (e) => toast(errorText(e), { kind: "error" }));
          },
        },
      });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  if (years.length === 0 || hidden) return null;
  const viewing = open ? years[open.year] : undefined;
  return (
    <section className="memories" aria-labelledby={`${id}-kop`}>
      <div className="memory-head">
        <h2 id={`${id}-kop`}>{t("photos_people.memories.title")}</h2>
        <Button variant="link" onClick={() => setPicking(true)}>
          {t("photos_people.memories.hideFolder")}
        </Button>
      </div>
      {years.map((y, yi) => (
        <div key={y.day} className="memory-year">
          <div className="memory-head">
            <h3 id={`${id}-${yi}`}>{t("photos_people.memories.yearsAgo", { n: y.years_ago })}</h3>
            <Button variant="link" onClick={() => void hide(y)}>
              {t("photos_people.memories.hide")}
            </Button>
          </div>
          <ul className="memory-strip" aria-labelledby={`${id}-${yi}`}>
            {y.items.map((it, i) => (
              <li key={it.id}>
                <MemoryPhoto it={it} onOpen={() => setOpen({ year: yi, index: i })} />
              </li>
            ))}
          </ul>
        </div>
      ))}
      {picking && (
        <Suspense fallback={null}>
          <FolderPicker
            title={t("photos_people.memories.pickFolder")}
            action={t("photos_people.memories.pickFolderAction")}
            startId={null}
            check={(f) => {
              const problem = folderProblem(f);
              return problem && t(problem);
            }}
            onPick={(folderId, folder) => void hideFolder(folderId, folder)}
            onClose={() => setPicking(false)}
          />
        </Suspense>
      )}
      {open && viewing && (
        <Suspense fallback={null}>
          <Viewer
            items={viewing.items}
            index={Math.min(open.index, viewing.items.length - 1)}
            onIndexChange={(index) => setOpen({ year: open.year, index })}
            onClose={() => setOpen(null)}
          />
        </Suspense>
      )}
    </section>
  );
}

/** Een foto van een jaar: een knop met de naam en de datum. De thumbnail vraagt een plek in de
 * wachtrij van het raster, net als een cel van de tijdlijn; eenmaal getoond blijft hij staan. */
function MemoryPhoto({ it, onOpen }: { it: PhotoItem; onOpen: () => void }): JSX.Element {
  const [ready, setReady] = useState(false);
  const want = !!it.thumb && !ready;
  useEffect(() => (want ? requestImage(() => setReady(true)) : undefined), [want]);
  return (
    <button
      type="button"
      className="memory-photo"
      aria-label={t("photos_people.memories.photo", { name: it.name, date: wallLabel(it.taken_at) })}
      onClick={onOpen}
    >
      {ready && it.thumb && <img src={it.thumb} alt="" loading="lazy" decoding="async" draggable={false} />}
    </button>
  );
}
