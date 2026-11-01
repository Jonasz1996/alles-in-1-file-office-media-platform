// /beheer/media (fase 5, van M10; Keuze 45): de worker, ffmpeg, de meting, het budget, wie er
// kijkt (met Stop), de redenen van 7 dagen, de achtergrond, de bibliotheken, de cache, de online
// metadata met de sleutel van TMDB, de import, de instellingen en de belastingtest (§4.6).
// Beheer.tsx (de haak van M0) toont het tabblad alleen als GET /api/v1/admin/media antwoordt,
// en laadt dit chunk pas bij het tabblad zelf. Ververst elke 5 seconden zolang het tabblad
// zichtbaar is; de voortgang van de import komt ook live binnen (event media.import).
//
// Alles is met het toetsenbord te doen: gewone knoppen en formulieren, Alt+8 naar het tabblad
// (docs/fase-5/toetsenbord-m10.md).

import { type FormEvent, type JSX, type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { api, type FileNode } from "../api";
import { useCurrentUser } from "../currentUser";
import { liveEvents, type ServerEvent } from "../events";
import { FolderPicker } from "../FolderPicker";
import { errorText, fmt, loadAreas, type MessageKey, t } from "../i18n";
import {
  clearCache,
  deleteTmdbKey,
  getAdmin,
  getLoadtest,
  importSources,
  putConfig,
  retryFailed,
  setTmdbKey,
  startBench,
  startImport,
  startLoadtest,
  stopSession,
  testTmdb,
} from "../media/api";
import type {
  CacheClearIn,
  ImportSource,
  ImportView,
  LoadtestView,
  MediaAdmin,
  MediaConfig,
  MediaConfigIn,
  SessionView,
  WorkClass,
} from "../media/types";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";
import { type AdminUser, adminApi, type Notice } from "./api";
import { noticeText } from "./format";
import { usePolling } from "./usePolling";

// --- wat de tests los toetsen (admin/media.test.ts) ----------------------------------------------

/** De getallen van MediaConfig met hun grenzen (app/config.py). max_transcodes kan op de server
 * lager zijn (het plafond uit env); dat zegt de server met 422 en params.max. */
export const CONFIG_LIMITS = {
  cpu_budget: { min: 0.5, max: 16, step: 0.5 },
  max_transcodes: { min: 0, max: 16, step: 1 },
  max_user_transcodes: { min: 1, max: 16, step: 1 },
  cache_gb: { min: 1, max: 10_000, step: 1 },
} as const;

export type ConfigNumber = keyof typeof CONFIG_LIMITS;

/** Een getal uit een invoerveld (een komma mag ook); undefined als het buiten de grenzen valt of
 * geen getal is. Hele getallen voor alles behalve cpu_budget. */
export function parseConfigNumber(field: ConfigNumber, text: string): number | undefined {
  const clean = text.trim().replace(",", ".");
  if (!/^\d+(\.\d+)?$/.test(clean)) return undefined;
  const n = Number(clean);
  const { min, max } = CONFIG_LIMITS[field];
  if (!Number.isFinite(n) || n < min || n > max) return undefined;
  if (field !== "cpu_budget" && !Number.isInteger(n)) return undefined;
  return n;
}

/** De kolommen van de redenen: alleen die er de afgelopen 7 dagen waren, het vaakst eerst. */
export function reasonColumns(days: MediaAdmin["reasons"]): string[] {
  const total = new Map<string, number>();
  for (const day of days) {
    for (const [reason, n] of Object.entries(day.counts)) total.set(reason, (total.get(reason) ?? 0) + (n ?? 0));
  }
  return [...total.entries()]
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([reason]) => reason);
}

/** Het event media.import over de import die er al stond: de lijst van wat overgeslagen is
 * blijft (het event heeft die niet), de tellers en de staat komen uit het event. Een andere job
 * vervangt alles. */
export function mergeImport(current: ImportView | null, data: Record<string, unknown>): ImportView | null {
  if (typeof data.job !== "string" || typeof data.state !== "string") return current;
  const event = data as unknown as ImportView;
  if (current && current.job === event.job) {
    return { ...current, ...event, skipped_items: event.skipped_items ?? current.skipped_items };
  }
  return { ...event, skipped_items: event.skipped_items ?? [] };
}

/** De belastingtest staat uit op de server (503 media_off met reason loadtest). */
export function loadtestOff(e: unknown): boolean {
  const err = (typeof e === "object" && e !== null ? e : {}) as { code?: unknown; params?: Record<string, unknown> };
  return err.code === "media_off" && err.params?.reason === "loadtest";
}

interface Phase {
  skipped?: string;
  passed?: boolean;
  stalls?: number;
  problems?: string[];
}

/** De regels van het rapport van de belastingtest (§4.6 punt 6 en 7), in de taal van de app. De
 * problemen zelf zijn tekst van de server (Nederlands). */
export function loadtestLines(report: Record<string, unknown> | null): string[] {
  if (!report) return [];
  const lines: string[] = [];
  const phases = (report.phases ?? {}) as Record<string, Phase>;
  for (const [phase, p] of Object.entries(phases)) {
    if (p.skipped) {
      lines.push(t("admin.media.loadtest.phaseSkipped", { phase, why: p.skipped }));
      continue;
    }
    const result = p.passed
      ? t("admin.media.loadtest.passed")
      : [t("admin.media.loadtest.notPassed"), ...(p.problems ?? [])].join(": ");
    lines.push(t("admin.media.loadtest.phaseResult", { phase, stalls: p.stalls ?? 0, result }));
  }
  if (typeof report.api_p95_ms === "number") {
    lines.push(t("admin.media.loadtest.apiP95", { ms: fmt.number(report.api_p95_ms, { maximumFractionDigits: 1 }) }));
  }
  if (typeof report.stopped === "string" && report.stopped) {
    lines.push(t("admin.media.loadtest.stoppedBecause", { why: report.stopped }));
  }
  if (typeof report.error === "string") lines.push(report.error);
  lines.push(
    t("admin.media.loadtest.result", {
      result: report.passed ? t("admin.media.loadtest.passed") : t("admin.media.loadtest.notPassed"),
    }),
  );
  return lines;
}

/** De doelmap van de import: een gekozen map van jezelf, anders de hoofdmap van de eigenaar. */
export function importTarget(
  owner: Pick<AdminUser, "id" | "root_id"> | null,
  me: { id: string } | null,
  picked: { id: string } | null,
): string | null {
  if (!owner) return null;
  if (picked && me && owner.id === me.id) return picked.id;
  return owner.root_id || null;
}

function badge(state: MediaAdmin["worker"]["state"]): "ok" | "degraded" | "down" {
  return state === "ok" ? "ok" : state === "down" ? "down" : "degraded";
}

const CLASSES: readonly WorkClass[] = ["transcode", "remux", "background", "short", "control"];
const CLEARABLE: readonly CacheClearIn["what"][] = ["sessions", "audio", "trick"];
const PARTS = ["sessions", "subs", "trick", "art", "audio"] as const;

// --- het tabblad -----------------------------------------------------------------------------------

interface Data {
  admin: MediaAdmin;
  loadtest: LoadtestView | null;
}

async function load(): Promise<Data> {
  const [admin, loadtest] = await Promise.all([getAdmin(), getLoadtest().catch(() => null)]);
  return { admin, loadtest };
}

type Reload = () => Promise<void>;

export default function MediaTab(): JSX.Element {
  const polled = usePolling(load, 5000);
  const data = polled.data;
  const reload = polled.reload;
  // De voortgang van de import live (event media.import), tot de volgende ververs
  const [live, setLive] = useState<ImportView | null>(null);
  useEffect(() => setLive(null), [data]);
  const current = data?.admin.import ?? null;
  useEffect(
    () =>
      liveEvents.subscribe((event: ServerEvent) => {
        if (event.type !== "media.import") return;
        setLive((cur) => mergeImport(cur ?? current, event.data));
        if (event.data.state === "done" || event.data.state === "failed") void reload();
      }),
    [current, reload],
  );

  return (
    <section aria-labelledby="admin-media-title">
      <h2 id="admin-media-title">
        {t("admin.media.title")}
        {data && (
          <>
            {": "}
            <span className={`badge ${badge(data.admin.worker.state)}`} data-media="worker-state">
              {t(`admin.media.state.${data.admin.worker.state}`)}
            </span>
          </>
        )}
      </h2>
      {polled.error !== null && <p role="alert">{t("admin.refreshFailed", { error: errorText(polled.error) })}</p>}
      {!data && polled.error === null && <p className="muted">{t("admin.media.loading")}</p>}
      {data && <Body data={data} imported={live ?? current} reload={reload} />}
    </section>
  );
}

function Body({ data, imported, reload }: { data: Data; imported: ImportView | null; reload: Reload }): JSX.Element {
  const a = data.admin;
  return (
    <>
      {!a.config.enabled && <p role="status">{t("admin.media.disabled")}</p>}
      {a.notices.length > 0 && (
        <section aria-labelledby="admin-media-notices">
          <h3 id="admin-media-notices">{t("admin.media.notices")}</h3>
          <ul className="admin-notices" data-media="notices">
            {a.notices.map((n) => (
              <li key={n.code}>{noticeText(n as Notice)}</li>
            ))}
          </ul>
        </section>
      )}
      <Worker a={a} />
      <Ffmpeg a={a} />
      <Bench a={a} reload={reload} />
      <Sessions a={a} reload={reload} />
      <Reasons a={a} />
      <Jobs a={a} reload={reload} />
      <Libraries a={a} reload={reload} />
      <Cache a={a} reload={reload} />
      <Providers a={a} reload={reload} />
      <Import imported={imported} reload={reload} />
      <Settings config={a.config} reload={reload} />
      <Loadtest view={data.loadtest} reload={reload} />
    </>
  );
}

/**
 * De focus na een knop die iets deed (zoals in OfficeTab): gaat de knop uit of weg, dan valt de
 * focus naar <body>. Dan terug op de knop als hij er nog is, en anders op de kop van het blok.
 */
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

/** Een knop die iets doet en daarna ververst: bezig, een fout onder het blok, een toast. */
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
  return { busy, error, setError, press, run };
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

function Worker({ a }: { a: MediaAdmin }): JSX.Element {
  const w = a.worker;
  const self = w.selftest;
  const selftest =
    self === null
      ? t("admin.media.worker.selftestNone")
      : !self.ok
        ? t("admin.media.worker.selftestFailed", { problems: self.problems.join("; ") })
        : self.sandbox === "off"
          ? t("admin.media.worker.selftestOff")
          : t("admin.media.worker.selftestOk");
  const children = w.children ?? [];
  return (
    <section aria-labelledby="admin-media-worker">
      <Head id="admin-media-worker" title={t("admin.media.worker.title")} />
      <ul className="admin-cards">
        <li className="admin-card">
          <h4 data-media="worker-mode">{t(`admin.media.worker.mode.${w.mode}`)}</h4>
          <p>{t("admin.media.worker.since", { time: fmt.dateTime(w.since) })}</p>
          <p className={self && !self.ok ? "badge down" : undefined}>{selftest}</p>
        </li>
        <li className="admin-card">
          <h4>{t("admin.media.worker.places")}</h4>
          <dl className="admin-totals">
            {CLASSES.map((c) => {
              const use = w.places[c];
              if (!use) return null;
              return (
                <div key={c}>
                  <dt>{t(`admin.media.worker.classes.${c}`)}</dt>
                  <dd data-media={`place-${c}`}>{t("admin.media.worker.place", { used: use.used, max: use.max })}</dd>
                </div>
              );
            })}
          </dl>
        </li>
        <li className="admin-card">
          <h4>{t("admin.media.worker.memory", { size: fmt.bytes((w.rss_kb ?? 0) * 1024) })}</h4>
          {children.length === 0 ? (
            <p className="muted">{t("admin.media.worker.noChildren")}</p>
          ) : (
            <ul>
              {children.map((c) => (
                <li key={c.pid}>
                  {t("admin.media.worker.child", { name: c.name, pid: c.pid, size: fmt.bytes(c.rss_kb * 1024) })}
                </li>
              ))}
            </ul>
          )}
          <p className="muted">{t("admin.media.worker.memoryNote")}</p>
        </li>
      </ul>
    </section>
  );
}

function Ffmpeg({ a }: { a: MediaAdmin }): JSX.Element {
  const f = a.ffmpeg;
  return (
    <section aria-labelledby="admin-media-ffmpeg">
      <Head id="admin-media-ffmpeg" title={t("admin.media.ffmpeg.title")} />
      {f === null ? (
        <p className="muted">{t("admin.media.ffmpeg.unknown")}</p>
      ) : (
        <dl className="admin-totals">
          <div>
            <dt>{t("admin.media.ffmpeg.version")}</dt>
            <dd data-media="ffmpeg-version">{f.version ?? t("admin.media.ffmpeg.none")}</dd>
          </div>
          <div>
            <dt>{t("admin.media.ffmpeg.missing")}</dt>
            <dd className={f.missing.length ? "badge down" : undefined}>
              {f.missing.length ? f.missing.join(", ") : t("admin.media.ffmpeg.nothing")}
            </dd>
          </div>
          <div>
            <dt>{t("admin.media.ffmpeg.hwaccel")}</dt>
            <dd>{f.hwaccel.length ? f.hwaccel.join(", ") : t("admin.media.ffmpeg.none")}</dd>
          </div>
          <div>
            <dt>{t("admin.media.ffmpeg.gpu")}</dt>
            <dd>{f.gpu ?? t("admin.media.ffmpeg.noGpu")}</dd>
          </div>
          <div>
            <dt>{t("admin.media.ffmpeg.encoders", { n: f.encoders.length })}</dt>
            <dd>{t("admin.media.ffmpeg.filters", { n: f.filters.length })}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

function Bench({ a, reload }: { a: MediaAdmin; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const b = a.bench;
  const used = fmt.number(a.budget.used, { maximumFractionDigits: 1 });
  const total = fmt.number(a.budget.total, { maximumFractionDigits: 1 });
  const reloads = a.native_reloads ?? 0;
  return (
    <section aria-labelledby="admin-media-bench">
      <Head id="admin-media-bench" title={t("admin.media.bench.title")} heading={heading}>
        <Button
          busy={act.busy === "bench"}
          onClick={() => {
            act.press();
            void act.run("bench", async () => {
              await startBench();
              return t("admin.media.bench.started");
            });
          }}
        >
          {t("admin.media.bench.start")}
        </Button>
      </Head>
      <p data-media="bench">
        {b
          ? t("admin.media.bench.value", {
              f: fmt.number(b.f, { maximumFractionDigits: 2 }),
              fdec: fmt.number(b.f_dec, { maximumFractionDigits: 2 }),
              time: fmt.dateTime(b.at),
            })
          : t("admin.media.bench.none")}
      </p>
      <p className="muted">{t("admin.media.bench.help")}</p>
      {act.error && <p role="alert">{act.error}</p>}
      <h3>{t("admin.media.budget.title")}</h3>
      <p data-media="budget">{t("admin.media.budget.value", { used, total })}</p>
      {a.budget.ds_starting && <p className="muted">{t("admin.media.budget.dsStarting")}</p>}
      {reloads > 0 && <p className="muted">{t("admin.media.budget.nativeReloads", { n: reloads })}</p>}
    </section>
  );
}

function sessionUser(s: SessionView): string {
  if (s.link_id && !s.user) return t("admin.media.sessions.publicLink");
  return s.user?.display_name || t("admin.media.sessions.unknownUser");
}

function Sessions({ a, reload }: { a: MediaAdmin; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);

  async function stop(s: SessionView, user: string) {
    act.press();
    const yes = await confirm({
      title: t("admin.media.sessions.stopTitle"),
      message: t("admin.media.sessions.stopText", { user }),
      confirm: t("admin.media.sessions.stop"),
      danger: true,
    });
    if (!yes) return;
    await act.run(s.id, async () => {
      await stopSession(s.id);
      return t("admin.media.sessions.stopped", { user });
    });
  }

  return (
    <section aria-labelledby="admin-media-sessions">
      <Head id="admin-media-sessions" title={t("admin.media.sessions.title")} heading={heading} />
      {act.error && <p role="alert">{act.error}</p>}
      {a.sessions.length === 0 ? (
        <p className="muted">{t("admin.media.sessions.none")}</p>
      ) : (
        <div className="table-wrap">
          <table data-media="sessions">
            <thead>
              <tr>
                <th>{t("admin.media.sessions.user")}</th>
                <th>{t("admin.media.sessions.what")}</th>
                <th>{t("admin.media.sessions.mode")}</th>
                <th className="wide-only">{t("admin.media.sessions.speed")}</th>
                <th className="wide-only">{t("admin.media.sessions.ahead")}</th>
                <th className="wide-only">{t("admin.media.sessions.since")}</th>
                <th>
                  <span className="sr-only">{t("admin.media.sessions.stop")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {a.sessions.map((s) => {
                const user = sessionUser(s);
                const title = s.title || t("admin.media.sessions.unknownTitle");
                const rung = s.rung ? ` ${t("admin.media.sessions.rung", { height: s.rung.height })}` : "";
                return (
                  <tr key={s.id}>
                    <td>{user}</td>
                    <td className="wrap">{title}</td>
                    <td>
                      {t(`admin.media.sessions.modes.${s.mode}`)}
                      {rung}
                    </td>
                    <td className="wide-only">
                      {s.speed != null
                        ? t("admin.media.sessions.speedValue", { x: fmt.number(s.speed, { maximumFractionDigits: 1 }) })
                        : ""}
                    </td>
                    <td className="wide-only">
                      {s.ahead_s != null
                        ? t("admin.media.sessions.aheadValue", { s: fmt.number(s.ahead_s, { maximumFractionDigits: 0 }) })
                        : ""}
                    </td>
                    <td className="wide-only">{fmt.time(s.created_at)}</td>
                    <td>
                      <Button
                        variant="danger"
                        busy={act.busy === s.id}
                        disabled={act.busy !== null && act.busy !== s.id}
                        aria-label={t("admin.media.sessions.stopLabel", { title, user })}
                        onClick={() => void stop(s, user)}
                      >
                        {t("admin.media.sessions.stop")}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Reasons({ a }: { a: MediaAdmin }): JSX.Element {
  const columns = reasonColumns(a.reasons);
  return (
    <section aria-labelledby="admin-media-reasons">
      <Head id="admin-media-reasons" title={t("admin.media.reasons.title")} />
      {columns.length === 0 ? (
        <p className="muted">{t("admin.media.reasons.none")}</p>
      ) : (
        <div className="table-wrap">
          <table data-media="reasons">
            <thead>
              <tr>
                <th>{t("admin.media.reasons.day")}</th>
                {columns.map((r) => (
                  <th key={r}>{t(`admin.media.reasons.names.${r}` as MessageKey)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {a.reasons.map((day) => (
                <tr key={day.day}>
                  <td>{fmt.date(day.day)}</td>
                  {columns.map((r) => (
                    <td key={r}>{fmt.number((day.counts as Record<string, number | undefined>)[r] ?? 0)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Jobs({ a, reload }: { a: MediaAdmin; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  return (
    <section aria-labelledby="admin-media-jobs">
      <Head id="admin-media-jobs" title={t("admin.media.jobs.title")} heading={heading}>
        {a.jobs.failed > 0 && (
          <Button
            busy={act.busy === "retry"}
            onClick={() => {
              act.press();
              void act.run("retry", async () => {
                const out = await retryFailed();
                return t("admin.media.jobs.retried", { n: out.retried });
              });
            }}
          >
            {t("admin.media.jobs.retry")}
          </Button>
        )}
      </Head>
      <p data-media="jobs">{t("admin.media.jobs.text", { ...a.jobs })}</p>
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}

function rescan(id: string): Promise<unknown> {
  // M10: de route van /beheer (ook van een ander); media/api.ts (M0) kent hem niet
  return api.request<unknown>("POST", `/api/v1/admin/media/libraries/${encodeURIComponent(id)}/scan`);
}

function Libraries({ a, reload }: { a: MediaAdmin; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  return (
    <section aria-labelledby="admin-media-libraries">
      <Head id="admin-media-libraries" title={t("admin.media.libraries.title")} heading={heading} />
      {act.error && <p role="alert">{act.error}</p>}
      {a.libraries.length === 0 ? (
        <p className="muted">{t("admin.media.libraries.none")}</p>
      ) : (
        <div className="table-wrap">
          <table data-media="libraries">
            <thead>
              <tr>
                <th>{t("admin.media.libraries.name")}</th>
                <th>{t("admin.media.libraries.owner")}</th>
                <th className="wide-only">{t("admin.media.libraries.kind")}</th>
                <th>{t("admin.media.libraries.count")}</th>
                <th className="wide-only">{t("admin.media.libraries.scanned")}</th>
                <th className="wide-only">{t("admin.media.libraries.errors")}</th>
                <th className="wide-only">{t("admin.media.libraries.low")}</th>
                <th>
                  <span className="sr-only">{t("admin.media.libraries.rescan")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {a.libraries.map((lib) => (
                <tr key={lib.id}>
                  <td className="wrap">
                    {lib.name}
                    {lib.inside && <p className="badge degraded">{t("admin.media.libraries.inside", { name: lib.inside })}</p>}
                  </td>
                  <td>{lib.owner.display_name}</td>
                  <td className="wide-only">{t(`admin.media.libraries.kinds.${lib.kind}`)}</td>
                  <td>{fmt.number(lib.item_count)}</td>
                  <td className="wide-only">
                    {lib.scanned_at ? fmt.dateTime(lib.scanned_at) : t("admin.media.libraries.never")}
                  </td>
                  <td className="wide-only">{fmt.number(lib.errors)}</td>
                  <td className="wide-only">{fmt.number(lib.low_confidence)}</td>
                  <td>
                    <Button
                      busy={act.busy === lib.id}
                      disabled={act.busy !== null && act.busy !== lib.id}
                      aria-label={t("admin.media.libraries.rescanLabel", { name: lib.name })}
                      onClick={() => {
                        act.press();
                        void act.run(lib.id, async () => {
                          await rescan(lib.id);
                          return t("admin.media.libraries.rescanned", { name: lib.name });
                        });
                      }}
                    >
                      {t("admin.media.libraries.rescan")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Cache({ a, reload }: { a: MediaAdmin; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);

  async function clear(what: CacheClearIn["what"], part: string) {
    act.press();
    const yes = await confirm({
      title: t("admin.media.cache.clearTitle", { part }),
      message: t("admin.media.cache.clearText"),
      confirm: t("admin.media.cache.clear"),
    });
    if (!yes) return;
    await act.run(what, async () => {
      await clearCache({ what });
      return t("admin.media.cache.cleared", { part });
    });
  }

  return (
    <section aria-labelledby="admin-media-cache">
      <Head id="admin-media-cache" title={t("admin.media.cache.title")} heading={heading} />
      <p>{t("admin.media.cache.free", { size: fmt.bytes(a.cache.free_bytes) })}</p>
      {act.error && <p role="alert">{act.error}</p>}
      <div className="table-wrap">
        <table data-media="cache">
          <tbody>
            {PARTS.map((part) => {
              const what = CLEARABLE.find((w) => w === part);
              const label = t(`admin.media.cache.parts.${part}`);
              return (
                <tr key={part}>
                  <th scope="row">{label}</th>
                  <td>{fmt.bytes(a.cache.parts[part] ?? 0)}</td>
                  <td>
                    {what && (
                      <Button
                        busy={act.busy === what}
                        disabled={act.busy !== null && act.busy !== what}
                        aria-label={t("admin.media.cache.clearLabel", { part: label })}
                        onClick={() => void clear(what, label)}
                      >
                        {t("admin.media.cache.clear")}
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted">{t("admin.media.cache.artNote")}</p>
    </section>
  );
}

function Providers({ a, reload }: { a: MediaAdmin; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const [key, setKey] = useState("");
  const tmdb = a.providers.find((p) => p.name === "tmdb");

  function save(e: FormEvent) {
    e.preventDefault();
    const value = key.trim();
    if (!value) return;
    void act.run("save", async () => {
      await setTmdbKey({ key: value });
      setKey(""); // de sleutel blijft nergens staan, ook niet in het veld
      return t("admin.media.providers.saved");
    });
  }

  async function remove() {
    act.press();
    const yes = await confirm({
      title: t("admin.media.providers.removeTitle"),
      message: t("admin.media.providers.removeText"),
      confirm: t("admin.media.providers.remove"),
      danger: true,
    });
    if (!yes) return;
    await act.run("remove", async () => {
      await deleteTmdbKey();
      return t("admin.media.providers.removed");
    });
  }

  return (
    <section aria-labelledby="admin-media-providers">
      <Head id="admin-media-providers" title={t("admin.media.providers.title")} heading={heading} />
      <div className="table-wrap">
        <table data-media="providers">
          <thead>
            <tr>
              <th>{t("admin.media.providers.name")}</th>
              <th>{t("admin.media.providers.key")}</th>
              <th className="wide-only">{t("admin.media.providers.lastOk")}</th>
              <th>{t("admin.media.providers.lastError")}</th>
              <th className="wide-only">{t("admin.media.providers.breaker")}</th>
            </tr>
          </thead>
          <tbody>
            {a.providers.map((p) => (
              <tr key={p.name}>
                <th scope="row">{t(`admin.media.providers.names.${p.name}`)}</th>
                <td>
                  {p.name !== "tmdb"
                    ? t("admin.media.providers.notNeeded")
                    : p.key_set
                      ? t("admin.media.providers.yes")
                      : t("admin.media.providers.no")}
                </td>
                <td className="wide-only">{p.last_ok ? fmt.dateTime(p.last_ok) : t("admin.media.providers.never")}</td>
                <td className="wrap">{p.last_error ?? ""}</td>
                <td className="wide-only">{t(`admin.media.providers.breakers.${p.breaker}`)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>{t("admin.media.providers.tmdbTitle")}</h4>
      <form className="stack" onSubmit={save}>
        <Field label={t("admin.media.providers.tmdbKey")} hint={t("admin.media.providers.tmdbHelp")}>
          {(c) => (
            <input
              {...c}
              type="password"
              name="tmdb-key"
              autoComplete="off"
              spellCheck={false}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          )}
        </Field>
        <div className="toolbar">
          <Button type="submit" variant="primary" busy={act.busy === "save"} disabled={!key.trim()}>
            {t("admin.media.providers.save")}
          </Button>
          {tmdb?.key_set && (
            <>
              <Button
                busy={act.busy === "test"}
                onClick={() => {
                  act.press();
                  void act.run("test", async () => {
                    await testTmdb();
                    return t("admin.media.providers.tested");
                  });
                }}
              >
                {t("admin.media.providers.test")}
              </Button>
              <Button variant="danger" busy={act.busy === "remove"} onClick={() => void remove()}>
                {t("admin.media.providers.remove")}
              </Button>
            </>
          )}
        </div>
      </form>
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}

function Import({ imported, reload }: { imported: ImportView | null; reload: Reload }): JSX.Element {
  const me = useCurrentUser();
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const [sources, setSources] = useState<ImportSource[] | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [source, setSource] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [move, setMove] = useState(false);
  const [picked, setPicked] = useState<FileNode | null>(null);
  const [picking, setPicking] = useState(false);

  // De mappenkiezer spreekt de woorden van Bestanden (het gebied "browser"); /beheer laadt alleen
  // "admin", dus eerst dat gebied, net als Nieuwe bibliotheek op /media (LibraryHome.tsx)
  async function openPicker() {
    try {
      await loadAreas(["browser"]);
      setPicking(true);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  const { setError } = act;

  useEffect(() => {
    let live = true;
    Promise.all([importSources(), adminApi.users()])
      .then(([found, people]) => {
        if (!live) return;
        setSources(found);
        setUsers(people.filter((u) => u.role !== "guest" && !u.disabled));
      })
      .catch((e: unknown) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [setError]);

  const ownerChoice = ownerId || me?.id || "";
  const owner = users.find((u) => u.id === ownerChoice) ?? null;
  const mine = owner !== null && me !== null && owner.id === me.id;
  const target = importTarget(owner, me, picked);
  const chosen = sources?.find((s) => s.path === (source || sources[0]?.path)) ?? null;
  const running = imported !== null && (imported.state === "queued" || imported.state === "running");

  function start(e: FormEvent) {
    e.preventDefault();
    if (!chosen || !owner || !target) return;
    void act.run("import", async () => {
      await startImport({ source: chosen.path, target_id: target, owner_id: owner.id, move });
      return t("admin.media.import.started");
    });
  }

  return (
    <section aria-labelledby="admin-media-import">
      <Head id="admin-media-import" title={t("admin.media.import.title")} heading={heading} />
      <p className="muted">{t("admin.media.import.help")}</p>
      <ImportProgress imported={imported} />
      {sources !== null && sources.length === 0 && <p>{t("admin.media.import.noSources")}</p>}
      {sources !== null && sources.length > 0 && (
        <form className="stack" onSubmit={start} data-media="import-form">
          <Field label={t("admin.media.import.source")}>
            {(c) => (
              <select {...c} name="source" value={chosen?.path ?? ""} onChange={(e) => setSource(e.target.value)}>
                {sources.map((s) => (
                  <option key={s.path} value={s.path}>
                    {t("admin.media.import.sourceOption", { path: s.path, files: s.files, size: fmt.bytes(s.bytes) })}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {chosen && chosen.unreadable > 0 && (
            <p className="muted">{t("admin.media.import.unreadable", { n: chosen.unreadable })}</p>
          )}
          {chosen && move && chosen.unmovable > 0 && (
            <p role="status">{t("admin.media.import.unmovable", { n: chosen.unmovable })}</p>
          )}
          <Field label={t("admin.media.import.owner")}>
            {(c) => (
              <select
                {...c}
                name="owner"
                value={ownerChoice}
                onChange={(e) => {
                  setOwnerId(e.target.value);
                  setPicked(null);
                }}
              >
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.display_name} ({u.email})
                  </option>
                ))}
              </select>
            )}
          </Field>
          <div className="field">
            <span id="admin-media-import-target">{t("admin.media.import.target")}</span>
            <p aria-labelledby="admin-media-import-target" data-media="import-target">
              {picked && mine
                ? t("admin.media.import.targetPicked", { name: picked.name })
                : t("admin.media.import.targetRoot", { name: owner?.display_name ?? "" })}
            </p>
            {mine && (
              <div className="toolbar">
                <Button onClick={() => void openPicker()}>{t("admin.media.import.targetPick")}</Button>
              </div>
            )}
            <small className="muted">{t("admin.media.import.targetHelp")}</small>
          </div>
          <fieldset>
            <legend>{t("admin.media.import.how")}</legend>
            <label className="check">
              <input type="radio" name="import-how" checked={!move} onChange={() => setMove(false)} />
              {t("admin.media.import.copy")}
            </label>
            <label className="check">
              <input type="radio" name="import-how" checked={move} onChange={() => setMove(true)} />
              {t("admin.media.import.move")}
            </label>
          </fieldset>
          <div className="toolbar">
            <Button
              type="submit"
              variant="primary"
              busy={act.busy === "import"}
              disabled={running || !target || !chosen}
            >
              {t("admin.media.import.start")}
            </Button>
          </div>
        </form>
      )}
      {act.error && <p role="alert">{act.error}</p>}
      {picking && me && (
        <FolderPicker
          title={t("admin.media.import.pickTitle")}
          action={t("admin.media.import.pickAction")}
          startId={me.root_id}
          check={(folder) => (folder.owner && folder.owner.id !== me.id ? t("admin.media.import.notYours") : null)}
          onPick={(_id, folder) => {
            setPicked(folder);
            setPicking(false);
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </section>
  );
}

function ImportProgress({ imported }: { imported: ImportView | null }): JSX.Element {
  if (!imported) return <p className="muted">{t("admin.media.import.noneRunning")}</p>;
  const tone = imported.state === "failed" ? "down" : imported.state === "done" ? "ok" : "degraded";
  return (
    <div data-media="import-progress">
      <p role="status">
        <span className={`badge ${tone}`} data-media="import-state">
          {t(`admin.media.import.states.${imported.state}`)}
        </span>{" "}
        {t("admin.media.import.current", { source: imported.source })}{" "}
        {t("admin.media.import.progress", {
          done: imported.done,
          total: imported.total,
          skipped: imported.skipped,
          size: fmt.bytes(imported.bytes),
        })}
      </p>
      {imported.skipped_items.length > 0 && (
        <details>
          <summary>{t("admin.media.import.skipped")}</summary>
          <ul>
            {imported.skipped_items.map((s) => (
              <li key={s.path}>
                {s.path}: {s.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

const NUMBERS: readonly { field: ConfigNumber; label: MessageKey }[] = [
  { field: "cpu_budget", label: "admin.media.settings.cpuBudget" },
  { field: "max_transcodes", label: "admin.media.settings.maxTranscodes" },
  { field: "max_user_transcodes", label: "admin.media.settings.maxUserTranscodes" },
  { field: "cache_gb", label: "admin.media.settings.cacheGb" },
];

type Switch = "enabled" | "public_transcode" | "hdr_1080" | "musicbrainz";

const SWITCHES: readonly { field: Switch; label: MessageKey }[] = [
  { field: "enabled", label: "admin.media.settings.enabled" },
  { field: "public_transcode", label: "admin.media.settings.publicTranscode" },
  { field: "hdr_1080", label: "admin.media.settings.hdr1080" },
  { field: "musicbrainz", label: "admin.media.settings.musicbrainz" },
];

type Form = Record<ConfigNumber, string> & Record<Switch, boolean>;

function formOf(c: MediaConfig): Form {
  return {
    cpu_budget: String(c.cpu_budget),
    max_transcodes: String(c.max_transcodes),
    max_user_transcodes: String(c.max_user_transcodes),
    cache_gb: String(c.cache_gb),
    enabled: c.enabled,
    public_transcode: c.public_transcode,
    hdr_1080: c.hdr_1080,
    musicbrainz: c.musicbrainz,
  };
}

function Settings({ config, reload }: { config: MediaConfig; reload: Reload }): JSX.Element {
  // Begint bij de waarden van de server; verversen laat staan wat de beheerder intypt
  const [form, setForm] = useState<Form>(() => formOf(config));
  const [problems, setProblems] = useState<Partial<Record<ConfigNumber, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    const found: Partial<Record<ConfigNumber, string>> = {};
    const body: MediaConfigIn = {
      enabled: form.enabled,
      public_transcode: form.public_transcode,
      hdr_1080: form.hdr_1080,
      musicbrainz: form.musicbrainz,
    };
    for (const { field } of NUMBERS) {
      const n = parseConfigNumber(field, form[field]);
      const { min, max } = CONFIG_LIMITS[field];
      if (n === undefined) found[field] = t("admin.media.settings.invalid", { min, max });
      else body[field] = n;
    }
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    try {
      const out = await putConfig(body);
      setForm(formOf(out));
      setError(null);
      toast(t("admin.media.settings.saved"), { kind: "ok" });
      await reload();
    } catch (err) {
      // Boven het plafond uit env: de server noemt het (422, params field en max)
      const params = (err as { params?: Record<string, unknown> }).params;
      if (params?.field === "max_transcodes" && typeof params.max === "number") {
        setProblems({ max_transcodes: t("admin.media.settings.invalid", { min: 0, max: params.max }) });
      }
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="admin-media-settings">
      <Head id="admin-media-settings" title={t("admin.media.settings.title")} />
      <form className="stack" onSubmit={save} data-media="settings">
        {SWITCHES.map(({ field, label }) => (
          <label key={field} className="check">
            <input
              type="checkbox"
              name={field}
              checked={form[field]}
              onChange={(e) => setForm({ ...form, [field]: e.target.checked })}
            />
            {t(label)}
          </label>
        ))}
        <p className="muted">{t("admin.media.settings.enabledHelp")}</p>
        {NUMBERS.map(({ field, label }) => (
          <Field
            key={field}
            label={t(label)}
            hint={
              field === "max_transcodes"
                ? t("admin.media.settings.ceiling")
                : t("admin.media.settings.between", { min: CONFIG_LIMITS[field].min, max: CONFIG_LIMITS[field].max })
            }
            error={problems[field] ?? null}
          >
            {(c) => (
              <input
                {...c}
                type="number"
                name={field}
                inputMode={field === "cpu_budget" ? "decimal" : "numeric"}
                min={CONFIG_LIMITS[field].min}
                max={CONFIG_LIMITS[field].max}
                step={CONFIG_LIMITS[field].step}
                value={form[field]}
                onChange={(e) => {
                  setForm({ ...form, [field]: e.target.value });
                  setProblems({ ...problems, [field]: undefined });
                }}
              />
            )}
          </Field>
        ))}
        <div className="toolbar">
          <Button type="submit" variant="primary" busy={busy}>
            {t("common.save")}
          </Button>
        </div>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

function Loadtest({ view, reload }: { view: LoadtestView | null; reload: Reload }): JSX.Element {
  const heading = useRef<HTMLHeadingElement>(null);
  const act = useAction(reload, heading);
  const [off, setOff] = useState(false);
  const [streams, setStreams] = useState("3");
  const [seconds, setSeconds] = useState("300");
  const running = view !== null && (view.state === "running" || view.state === "making_sources");

  function start(e: FormEvent) {
    e.preventDefault();
    const n = Math.min(3, Math.max(1, Number.parseInt(streams, 10) || 3));
    const s = Math.min(600, Math.max(30, Number.parseInt(seconds, 10) || 300));
    void act.run("loadtest", async () => {
      try {
        await startLoadtest({ streams: n, seconds: s, codec: "h264" });
      } catch (err) {
        if (!loadtestOff(err)) throw err;
        setOff(true);
        return null;
      }
      return t("admin.media.loadtest.started");
    });
  }

  const lines = loadtestLines(view?.report ?? null);
  return (
    <section aria-labelledby="admin-media-loadtest">
      <Head id="admin-media-loadtest" title={t("admin.media.loadtest.title")} heading={heading} />
      <p className="muted">{t("admin.media.loadtest.help")}</p>
      {view && (
        <p role="status" data-media="loadtest-state">
          {t(`admin.media.loadtest.states.${view.state}`)}
          {view.phase ? ` (${t("admin.media.loadtest.phase", { phase: view.phase })})` : ""}
        </p>
      )}
      {lines.length > 0 && (
        <ul data-media="loadtest-report">
          {lines.map((line, i) => (
            <li key={`${i}:${line}`}>{line}</li>
          ))}
        </ul>
      )}
      {off ? (
        <p role="alert">{t("admin.media.loadtest.off")}</p>
      ) : (
        <form className="stack" onSubmit={start}>
          <Field label={t("admin.media.loadtest.streams")}>
            {(c) => (
              <input
                {...c}
                type="number"
                name="streams"
                min={1}
                max={3}
                step={1}
                value={streams}
                onChange={(e) => setStreams(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("admin.media.loadtest.seconds")}>
            {(c) => (
              <input
                {...c}
                type="number"
                name="seconds"
                min={30}
                max={600}
                step={30}
                value={seconds}
                onChange={(e) => setSeconds(e.target.value)}
              />
            )}
          </Field>
          <div className="toolbar">
            <Button type="submit" busy={act.busy === "loadtest"} disabled={running}>
              {t("admin.media.loadtest.start")}
            </Button>
          </div>
        </form>
      )}
      {act.error && <p role="alert">{act.error}</p>}
    </section>
  );
}
