// "Vandaag, N jaar geleden" bovenaan /fotos (fase 6, van Z6; Keuze 41): per eerder jaar je foto's
// van deze dag (GET /photos/memories; de dag is die van de server). Een eigen chunk met het gebied
// photos_people; /fotos laadt hem alleen zonder filters. Niets te tonen (of herinneringen uit, of
// een fout): dan staat er niets.
//
// Toetsenbord: elke foto is een knop (Tab), Enter opent hem in de viewer (← en → door de foto's
// van dat jaar); "Deze dag verbergen" (met Ongedaan maken in de melding) zet de focus daarna op de
// kop van de pagina.
//
// Het blok staat boven de tijdlijn en mag die niet trager maken (de scroll-meting van e2e/perf):
// de thumbnails komen via de wachtrij van het raster (een paar per frame, niets tijdens snel
// scrollen), en een jaar buiten beeld kost niets (content-visibility in people.css).

import { type JSX, Suspense, useCallback, useEffect, useId, useState } from "react";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { focusPage } from "../ui/PageTitle";
import { toast } from "../ui/toast";
import { Viewer } from "../viewer";
import { type PhotoItem, wallLabel } from "./api";
import { requestImage } from "./Grid";
import { hideMemories, memories, unhideMemories } from "./people/api";
import type { MemoryYear } from "./people/types";
import "./people/people.css";

export default function Memories(): JSX.Element | null {
  const id = useId();
  const [years, setYears] = useState<MemoryYear[]>([]);
  const [open, setOpen] = useState<{ year: number; index: number } | null>(null);

  const load = useCallback(async () => {
    try {
      setYears((await memories()).years);
    } catch {
      setYears([]); // herinneringen zijn een extraatje: zonder blijft /fotos gewoon werken
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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

  if (years.length === 0) return null;
  const viewing = open ? years[open.year] : undefined;
  return (
    <section className="memories" aria-labelledby={`${id}-kop`}>
      <h2 id={`${id}-kop`}>{t("photos_people.memories.title")}</h2>
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
