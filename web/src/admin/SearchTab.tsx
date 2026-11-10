// De tab "Zoeken en foto's" op /beheer (fase 6, van Z7; Keuze 51): de motor, de stand van de
// index, mislukt, een herbouw met zijn voortgang, waar tekst gelezen wordt, het afgelopen uur,
// OCR, de plek en de duplicaten van foto's, de plaatsnamen en de uitkomst van bench --los. De
// knoppen: index herbouwen, mislukte opnieuw, achtergrondwerk pauzeren of hervatten, duplicaten en
// plek opnieuw, en OCR aan of uit (alleen als X1 zich aanmeldde). Wat zwaar is, vraagt eerst.
//
// Z0 zette de tab (Beheer.tsx, Alt+9, een eigen chunk); de aanroepen staan in ./searchApi. Ververst
// elke 5 seconden zolang het tabblad zichtbaar is (usePolling, zoals de andere tabbladen).
//
// Z0b (Keuze 55): onder het deel van Z7 de blokken "Gezichten" (FacesBlock.tsx, van X2) en "Zoeken
// op beeld" (BeeldBlock.tsx, van X3), in dezelfde chunk. Beheer.tsx laadt de gebieden
// photos_faces en beeld met de tab. Z7 houdt de twee blokken onderaan staan en geeft ze hun stand
// mee (null zolang X2 of X3 er niet is).
//
// Alles met het toetsenbord: gewone knoppen; na een knop blijft de focus op die knop, of gaat hij
// naar de kop van zijn deel als de knop weg is (docs/fase-6/toetsenbord-z7.md).

import { type JSX, type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { toast } from "../ui/toast";
import { BeeldBlock } from "./BeeldBlock";
import { FacesBlock } from "./FacesBlock";
import {
  type AdminSearch,
  adminSearch,
  type FailedItem,
  failedSearch,
  patchAdminSearch,
  type RebuildWhat,
  rebuildSearch,
  retrySearch,
} from "./searchApi";
import { usePolling } from "./usePolling";

// --- wat de tests los toetsen (admin/searchTab.test.ts) ------------------------------------------

/** Zo vaak ververst de tab, in ms (zoals media). */
export const POLL_MS = 5000;

/** De index wordt herbouwd (names of text): zoeken gaat dan met LIKE, en de knop is weg. */
export function indexRebuilding(s: AdminSearch): boolean {
  return !s.index.names_done || s.rebuild?.what === "names" || s.rebuild?.what === "text";
}

/** Een herbouw van de foto's loopt (hash of geo): dan zijn die twee knoppen weg. */
export function photosRebuilding(s: AdminSearch): boolean {
  return s.rebuild?.what === "hash" || s.rebuild?.what === "geo";
}

/** De tekst van de index als deel van 1 (0 tot 1); null zonder bestanden met tekst. */
export function textFraction(index: AdminSearch["index"]): number | null {
  if (index.text_total <= 0) return null;
  return Math.min(1, Math.max(0, index.text_done / index.text_total));
}

// --- het tabblad -----------------------------------------------------------------------------------

type Reload = () => Promise<void>;

export default function SearchTab(): JSX.Element {
  const polled = usePolling(adminSearch, POLL_MS);
  return <SearchView data={polled.data} error={polled.error} reload={polled.reload} />;
}

/** De tab bij een gegeven stand (los te toetsen zonder server). */
export function SearchView({
  data,
  error,
  reload,
}: {
  data: AdminSearch | null;
  error: unknown;
  reload: Reload;
}): JSX.Element {
  return (
    <>
      <section aria-labelledby="admin-search-title">
        <h2 id="admin-search-title">{t("admin.search.tab")}</h2>
        {error !== null && <p role="alert">{t("admin.refreshFailed", { error: errorText(error) })}</p>}
        {!data && error === null && <p className="muted">{t("admin.search.loading")}</p>}
        {data && <Body s={data} reload={reload} />}
      </section>
      {data && <FacesBlock faces={data.faces} />}
      {data && <BeeldBlock beeld={data.beeld} />}
    </>
  );
}

function Body({ s, reload }: { s: AdminSearch; reload: Reload }): JSX.Element {
  return (
    <>
      <Engine s={s} />
      <Pause s={s} reload={reload} />
      <Index s={s} reload={reload} />
      {s.rebuild && <RebuildStatus rebuild={s.rebuild} />}
      <Failed s={s} reload={reload} />
      <Photos s={s} reload={reload} />
      <Hour s={s} />
      <Ocr s={s} reload={reload} />
      <Places s={s} />
      <Bench s={s} />
    </>
  );
}

// --- focus en acties (zoals MediaTab) -------------------------------------------------------------

/** Na een actie: terug naar de knop die je indrukte, of naar de kop als die knop weg is. */
function useFocusAfter(busy: boolean, heading: RefObject<HTMLElement | null>): () => void {
  const pressed = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (busy) return;
    const el = pressed.current;
    pressed.current = null;
    const now = document.activeElement;
    if (el === null || (now !== null && now !== document.body)) return;
    (el.isConnected ? el : heading.current)?.focus();
  }, [busy, heading]);
  return () => {
    const now = document.activeElement;
    pressed.current = now instanceof HTMLElement ? now : null;
  };
}

function useAction(reload: Reload, heading: RefObject<HTMLElement | null>) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const press = useFocusAfter(busy !== null, heading);
  async function run(key: string, fn: () => Promise<string | null>): Promise<void> {
    setBusy(key);
    try {
      const message = await fn();
      setError(null);
      if (message) toast(message, { kind: "ok" });
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }
  return { busy, error, press, run };
}

function Head({
  id,
  title,
  heading,
  children,
}: {
  id: string;
  title: string;
  heading?: RefObject<HTMLHeadingElement | null>;
  children?: ReactNode;
}): JSX.Element {
  return (
    <div className="admin-head">
      <h3 id={id} ref={heading} tabIndex={heading ? -1 : undefined}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function Row({ label, children, testid }: { label: string; children: ReactNode; testid?: string }): JSX.Element {
  return (
    <div>
      <dt>{label}</dt>
      <dd data-search={testid}>{children}</dd>
    </div>
  );
}

// --- de delen ---------------------------------------------------------------------------------------

function Engine({ s }: { s: AdminSearch }): JSX.Element {
  return (
    <section aria-labelledby="admin-search-engine">
      <Head id="admin-search-engine" title={t("admin.search.engine.title")} />
      <dl className="admin-totals">
        <Row label={t("admin.search.engine.title")} testid="engine">
          <span className={`badge ${s.engine === "fts" ? "ok" : "degraded"}`}>{t(`admin.search.engine.${s.engine}`)}</span>
        </Row>
        <Row label={t("admin.search.engine.sandbox")} testid="sandbox">
          {s.extract_in_sandbox ? t("admin.search.engine.inSandbox") : t("admin.search.engine.outsideSandbox")}
        </Row>
      </dl>
      {s.engine === "like" && <p className="muted">{t("admin.search.engine.likeHelp")}</p>}
    </section>
  );
}

function Pause({ s, reload }: { s: AdminSearch; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const paused = s.paused;
  return (
    <section aria-labelledby="admin-search-pause">
      <Head id="admin-search-pause" title={t("admin.search.pause.title")} heading={heading}>
        <Button
          busy={act.busy === "pause"}
          aria-pressed={paused}
          data-search="pause-button"
          onClick={() => {
            act.press();
            void act.run("pause", async () => {
              await patchAdminSearch({ paused: !paused });
              return paused ? t("admin.search.pause.resumedToast") : t("admin.search.pause.pausedToast");
            });
          }}
        >
          {paused ? t("admin.search.pause.resume") : t("admin.search.pause.pause")}
        </Button>
      </Head>
      <p>
        <span className={`badge ${paused ? "degraded" : "ok"}`} data-search="paused">
          {paused ? t("admin.search.pause.paused") : t("admin.search.pause.running")}
        </span>
      </p>
      {paused && <p className="muted">{t("admin.search.pause.pausedText")}</p>}
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}

function Index({ s, reload }: { s: AdminSearch; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const fraction = textFraction(s.index);
  const total = s.index.text_total;
  return (
    <section aria-labelledby="admin-search-index">
      <Head id="admin-search-index" title={t("admin.search.index.title")} heading={heading}>
        {!indexRebuilding(s) && (
          <Button
            busy={act.busy === "rebuild"}
            data-search="rebuild-index"
            onClick={async () => {
              act.press();
              const yes = await confirm({
                title: t("admin.search.index.rebuildTitle"),
                message: t("admin.search.index.rebuildText"),
                confirm: t("admin.search.index.rebuildConfirm"),
              });
              if (!yes) return;
              await act.run("rebuild", async () => {
                await rebuildSearch("text");
                return t("admin.search.index.started");
              });
            }}
          >
            {t("admin.search.index.rebuild")}
          </Button>
        )}
      </Head>
      <dl className="admin-totals">
        <Row label={t("admin.search.index.names")} testid="names">
          {s.index.names_done ? t("admin.search.index.namesDone") : t("admin.search.index.namesBusy")}
        </Row>
        <Row label={t("admin.search.index.text")} testid="text">
          {t("admin.search.index.textValue", { n: total, done: fmt.number(s.index.text_done), total: fmt.number(total) })}
          {fraction !== null && (
            <>
              {" "}
              <progress max={1} value={fraction} aria-label={t("admin.search.index.textProgress")} />
            </>
          )}
        </Row>
        <Row label={t("admin.search.index.failed")} testid="failed">
          {t("admin.search.index.failedValue", { n: s.index.failed })}
        </Row>
      </dl>
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}

function RebuildStatus({ rebuild }: { rebuild: NonNullable<AdminSearch["rebuild"]> }): JSX.Element {
  return (
    <section aria-labelledby="admin-search-rebuild">
      <Head id="admin-search-rebuild" title={t("admin.search.rebuild.title")} />
      <p role="status" data-search="rebuild">
        {t("admin.search.rebuild.running", {
          what: t(`admin.search.rebuild.what.${rebuild.what}`),
          percent: fmt.percent(rebuild.done),
          time: fmt.dateTime(rebuild.started_at),
        })}{" "}
        <progress max={1} value={rebuild.done} aria-label={t("admin.search.rebuild.progress")} />
      </p>
    </section>
  );
}

function Failed({ s, reload }: { s: AdminSearch; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const [items, setItems] = useState<FailedItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const n = s.index.failed;

  async function page(after: string | null): Promise<string | null> {
    const found = await failedSearch(after);
    setItems((cur) => (after && cur ? [...cur, ...found.items] : found.items));
    setCursor(found.cursor);
    return null;
  }

  return (
    <section aria-labelledby="admin-search-failed">
      <Head id="admin-search-failed" title={t("admin.search.failed.title")} heading={heading}>
        {n > 0 && (
          <Button
            busy={act.busy === "retry"}
            data-search="retry"
            onClick={() => {
              act.press();
              void act.run("retry", async () => {
                await retrySearch();
                setItems(null);
                setCursor(null);
                return t("admin.search.failed.retried");
              });
            }}
          >
            {t("admin.search.failed.retry")}
          </Button>
        )}
      </Head>
      {n === 0 && items === null && <p className="muted">{t("admin.search.failed.none")}</p>}
      {(n > 0 || items !== null) && (
        <p>
          <Button
            busy={act.busy === "list"}
            aria-expanded={items !== null}
            data-search="failed-toggle"
            onClick={() => {
              if (items !== null) {
                setItems(null);
                setCursor(null);
                return;
              }
              act.press();
              void act.run("list", () => page(null));
            }}
          >
            {items === null ? t("admin.search.failed.show") : t("admin.search.failed.hide")}
          </Button>
        </p>
      )}
      {items !== null && (
        <>
          <p className="muted">{t("admin.search.failed.help")}</p>
          {items.length === 0 ? (
            <p className="muted">{t("admin.search.failed.none")}</p>
          ) : (
            <div className="table-wrap">
              <table data-search="failed-list">
                <thead>
                  <tr>
                    <th scope="col">{t("admin.search.failed.sha")}</th>
                    <th scope="col">{t("admin.search.failed.step")}</th>
                    <th scope="col">{t("admin.search.failed.code")}</th>
                    <th scope="col">{t("admin.search.failed.at")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={`${item.sha}:${item.step}`}>
                      <td>
                        <code>{item.sha}</code>
                      </td>
                      <td>{t(`admin.search.failed.steps.${item.step}`)}</td>
                      <td>
                        <code>{item.code}</code>
                      </td>
                      <td>{fmt.dateTime(item.at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {cursor !== null && (
            <p>
              <Button
                busy={act.busy === "more"}
                data-search="failed-more"
                onClick={() => {
                  act.press();
                  void act.run("more", () => page(cursor));
                }}
              >
                {t("admin.search.failed.more")}
              </Button>
            </p>
          )}
        </>
      )}
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}

function Photos({ s, reload }: { s: AdminSearch; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);

  async function start(what: Extract<RebuildWhat, "hash" | "geo">): Promise<void> {
    act.press();
    const yes = await confirm({
      title: t(`admin.search.photos.${what}Title`),
      message: t(`admin.search.photos.${what}Text`),
      confirm: t("admin.search.photos.start"),
    });
    if (!yes) return;
    await act.run(what, async () => {
      await rebuildSearch(what);
      return t("admin.search.index.started");
    });
  }

  return (
    <section aria-labelledby="admin-search-photos">
      <Head id="admin-search-photos" title={t("admin.search.photos.title")} heading={heading} />
      {!photosRebuilding(s) && (
        <div className="toolbar">
          <Button busy={act.busy === "hash"} data-search="rebuild-hash" onClick={() => void start("hash")}>
            {t("admin.search.photos.hash")}
          </Button>
          <Button busy={act.busy === "geo"} data-search="rebuild-geo" onClick={() => void start("geo")}>
            {t("admin.search.photos.geo")}
          </Button>
        </div>
      )}
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}

function Hour({ s }: { s: AdminSearch }): JSX.Element {
  return (
    <section aria-labelledby="admin-search-hour">
      <Head id="admin-search-hour" title={t("admin.search.hour.title")} />
      <dl className="admin-totals">
        <Row label={t("admin.search.hour.deadlineB")} testid="hour-b">
          {fmt.number(s.per_hour.deadline_b)}
        </Row>
        <Row label={t("admin.search.hour.deadlineC")} testid="hour-c">
          {fmt.number(s.per_hour.deadline_c)}
        </Row>
        <Row label={t("admin.search.hour.busy")} testid="hour-busy">
          {fmt.number(s.per_hour.busy)}
        </Row>
      </dl>
    </section>
  );
}

function Ocr({ s, reload }: { s: AdminSearch; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const ocr = s.ocr;

  async function toggle(): Promise<void> {
    act.press();
    if (!ocr.on) {
      const yes = await confirm({
        title: t("admin.search.ocr.onTitle"),
        message: t("admin.search.ocr.onText"),
        confirm: t("admin.search.ocr.onConfirm"),
      });
      if (!yes) return;
    }
    await act.run("ocr", async () => {
      await patchAdminSearch({ ocr: !ocr.on });
      return ocr.on ? t("admin.search.ocr.offToast") : t("admin.search.ocr.onToast");
    });
  }

  const yesNo = (v: boolean) => (v ? t("admin.search.ocr.yes") : t("admin.search.ocr.no"));
  return (
    <section aria-labelledby="admin-search-ocr">
      <Head id="admin-search-ocr" title={t("admin.search.ocr.title")} heading={heading}>
        {ocr.installed && (
          <Button busy={act.busy === "ocr"} aria-pressed={ocr.on} data-search="ocr-button" onClick={() => void toggle()}>
            {ocr.on ? t("admin.search.ocr.turnOff") : t("admin.search.ocr.turnOn")}
          </Button>
        )}
      </Head>
      <dl className="admin-totals" data-search="ocr">
        <Row label={t("admin.search.ocr.installed")}>{yesNo(ocr.installed)}</Row>
        <Row label={t("admin.search.ocr.on")}>{yesNo(ocr.on)}</Row>
        <Row label={t("admin.search.ocr.queued")}>{fmt.number(ocr.pages_queued)}</Row>
      </dl>
      {!ocr.installed && <p className="muted">{t("admin.search.ocr.notInstalled")}</p>}
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}

function Places({ s }: { s: AdminSearch }): JSX.Element {
  const p = s.places;
  return (
    <section aria-labelledby="admin-search-places">
      <Head id="admin-search-places" title={t("admin.search.places.title")} />
      <p data-search="places">
        {p.loaded
          ? t("admin.search.places.loaded", { source: p.source ?? "", date: p.date ? fmt.date(p.date) : "" })
          : t("admin.search.places.notLoaded")}
      </p>
    </section>
  );
}

function Bench({ s }: { s: AdminSearch }): JSX.Element {
  const b = s.bench;
  const ms = (v: number) => fmt.number(v, { maximumFractionDigits: 1 });
  return (
    <section aria-labelledby="admin-search-bench">
      <Head id="admin-search-bench" title={t("admin.search.bench.title")} />
      <p data-search="bench">
        {b
          ? t("admin.search.bench.value", { owner: ms(b.p95_owner_ms), all: ms(b.p95_all_ms), time: fmt.dateTime(b.at) })
          : t("admin.search.bench.none")}
      </p>
      <p className="muted">{t("admin.search.bench.help")}</p>
    </section>
  );
}
