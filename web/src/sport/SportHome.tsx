// /sport: je activiteiten (nieuwste eerst), de afstand van de laatste 12 weken en per maand, de
// totalen per sport, je beste tijden, en importeren (knop of slepen). De default export is de
// pagina van de route, in een eigen chunk.

import { type DragEvent, type JSX, useCallback, useEffect, useRef, useState } from "react";
import { errorText, fmt, locale, t } from "../i18n";
import { Link } from "../links";
import type { RouteProps } from "../router";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";
import { Spinner } from "../ui/Spinner";
import { toast } from "../ui/toast";
import {
  type ActivityItem,
  activities,
  type ImportOutcome,
  importFile,
  type Records,
  records,
  SPORTS,
  type SportConfig,
  type SportKind,
  type Stats,
  sportConfig,
  stats,
} from "./api";
import { Bars } from "./Bars";
import { duration, km, meters, sportLabel } from "./format";
import { SettingsDialog } from "./SettingsDialog";
import "./sport.css";

const ACCEPT = ".gpx,.tcx,.gz,.zip,application/gpx+xml,application/zip";

export function Preview({ points }: { points: string }): JSX.Element | null {
  if (!points) return null;
  const pts = points.split(" ").map((p) => p.split(",").map(Number) as [number, number]);
  const w = Math.max(1, ...pts.map((p) => p[0]));
  const h = Math.max(1, ...pts.map((p) => p[1]));
  return (
    <svg className="sport-preview" viewBox={`-40 -40 ${w + 80} ${h + 80}`} aria-hidden="true">
      <polyline points={points} />
    </svg>
  );
}

function ActivityCard({ a }: { a: ActivityItem }): JSX.Element {
  return (
    <li className="sport-card">
      <Preview points={a.preview} />
      <div className="sport-card-body">
        <Link to={`/sport/${a.id}`} className="sport-card-name">
          {a.name}
        </Link>
        <div className="muted">
          {sportLabel(a.sport)}
          {a.start_at && ` · ${fmt.dateTime(a.start_at)}`}
        </div>
        <dl className="sport-card-stats">
          <div>
            <dt>{t("sport.fields.distance")}</dt>
            <dd>{t("sport.units.km", { v: km(a.distance) })}</dd>
          </div>
          <div>
            <dt>{t("sport.fields.moving")}</dt>
            <dd>{duration(a.moving)}</dd>
          </div>
          <div>
            <dt>{t("sport.fields.gain")}</dt>
            <dd>{t("sport.units.m", { v: meters(a.gain) })}</dd>
          </div>
        </dl>
      </div>
    </li>
  );
}

export default function SportHome(_: RouteProps): JSX.Element {
  const [sport, setSport] = useState<SportKind | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<ActivityItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [st, setSt] = useState<Stats | null>(null);
  const [rec, setRec] = useState<Records | null>(null);
  const [config, setConfig] = useState<SportConfig | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<ImportOutcome[] | null>(null);
  const [dragging, setDragging] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const gen = useRef(0);

  const load = useCallback(() => {
    const mine = ++gen.current;
    setItems(null);
    setError(null);
    activities({ sport, year, q: q.trim() || null }).then(
      (page) => {
        if (gen.current !== mine) return;
        setItems(page.items);
        setCursor(page.cursor);
      },
      (e) => gen.current === mine && setError(errorText(e)),
    );
    stats(year, sport).then(setSt, () => setSt(null));
    records(sport && sport !== "other" ? sport : "run").then(setRec, () => setRec(null));
  }, [sport, year, q]);

  useEffect(() => {
    const id = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(id);
  }, [load, q]);

  useEffect(() => {
    sportConfig().then(setConfig, () => setConfig(null));
  }, []);

  async function more(): Promise<void> {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await activities({ sport, year, q: q.trim() || null, cursor });
      setItems((cur) => [...(cur ?? []), ...page.items]);
      setCursor(page.cursor);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setLoadingMore(false);
    }
  }

  async function importAll(files: readonly File[]): Promise<void> {
    if (!files.length) return;
    const all: ImportOutcome[] = [];
    for (const f of files) {
      setBusy(f.name);
      try {
        all.push(...(await importFile(f)));
      } catch (e) {
        all.push({ name: f.name, status: "error", id: null, reason: errorText(e) });
      }
    }
    setBusy(null);
    setResults(all);
    load();
  }

  function onDrop(e: DragEvent<HTMLDivElement>): void {
    e.preventDefault();
    setDragging(false);
    void importAll(Array.from(e.dataTransfer.files));
  }

  const n = (s: ImportOutcome["status"]) => results?.filter((r) => r.status === s).length ?? 0;
  const months = st?.months ?? [];
  const monthLabel = (r: { key: string }) =>
    new Intl.DateTimeFormat(locale(), { month: "narrow" }).format(new Date(`${r.key}-15T12:00:00`));
  const weekLabel = (r: { key: string }) =>
    new Intl.DateTimeFormat(locale(), { day: "numeric", month: "numeric" }).format(new Date(`${r.key}T12:00:00`));

  return (
    <div
      className={`sport-page${dragging ? " sport-dragging" : ""}`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <PageTitle
        actions={
          <>
            {config?.can_edit && (
              <Button icon="settings" onClick={() => setSettingsOpen(true)}>
                {t("sport.settings.button")}
              </Button>
            )}
            <Button variant="primary" icon="upload" busy={busy !== null} onClick={() => input.current?.click()}>
              {t("sport.import.button")}
            </Button>
          </>
        }
      >
        {t("sport.title")}
      </PageTitle>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          const files = Array.from(e.currentTarget.files ?? []);
          e.currentTarget.value = "";
          void importAll(files);
        }}
      />
      <p className="muted sport-intro">{t("sport.intro")}</p>
      {busy && (
        <p role="status" className="sport-busy">
          <Spinner inline /> {t("sport.import.busy", { name: busy })}
        </p>
      )}
      {dragging && <div className="sport-drop">{t("sport.import.drop")}</div>}

      <div className="sport-filters">
        <label>
          <span>{t("sport.filter.sport")}</span>
          <select value={sport ?? ""} onChange={(e) => setSport((e.target.value || null) as SportKind | null)}>
            <option value="">{t("sport.sports.all")}</option>
            {SPORTS.map((s) => (
              <option key={s} value={s}>
                {sportLabel(s)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{t("sport.filter.year")}</span>
          <select value={year ?? ""} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{t("sport.filter.allYears")}</option>
            {(st?.years ?? []).map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{t("sport.filter.search")}</span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
      </div>

      {st && st.all_time.count > 0 && (
        <div className="sport-stats">
          <Bars title={t("sport.stats.weeks")} rows={st.weeks} label={weekLabel} />
          <Bars title={t("sport.stats.year", { year: st.year })} rows={months} label={monthLabel} />
          <section className="sport-totals">
            <h2>{t("sport.stats.perSport", { year: st.year })}</h2>
            <ul>
              {st.per_sport.map((s) => (
                <li key={s.key}>
                  <strong>{sportLabel(s.key as SportKind)}</strong>
                  <span>
                    {t("sport.stats.count", { n: s.count })} · {t("sport.units.km", { v: km(s.distance, 0) })} ·{" "}
                    {duration(s.moving)} · {t("sport.units.m", { v: meters(s.gain) })}
                  </span>
                </li>
              ))}
              <li>
                <strong>{t("sport.stats.allTime")}</strong>
                <span>
                  {t("sport.stats.count", { n: st.all_time.count })} ·{" "}
                  {t("sport.units.km", { v: km(st.all_time.distance, 0) })} · {duration(st.all_time.moving)}
                </span>
              </li>
            </ul>
          </section>
          {rec && rec.rows.length > 0 && (
            <section className="sport-totals">
              <h2>
                {t("sport.stats.records")} · {sportLabel(rec.sport)}
              </h2>
              {rec.rows.some((r) => r.best.length) ? (
                <ul>
                  {rec.rows
                    .filter((r) => r.best.length)
                    .map((r) => (
                      <li key={r.distance}>
                        <strong>{t("sport.units.km", { v: km(r.distance, r.distance % 1000 ? 2 : 0) })}</strong>
                        <span>
                          <Link to={`/sport/${r.best[0].id}`}>{duration(r.best[0].seconds)}</Link>
                          {r.best[0].day && ` · ${fmt.date(`${r.best[0].day}T12:00:00`)}`}
                        </span>
                      </li>
                    ))}
                </ul>
              ) : (
                <p className="muted">{t("sport.stats.none")}</p>
              )}
            </section>
          )}
        </div>
      )}

      {error && <p role="alert">{error}</p>}
      {items === null && !error && <Spinner />}
      {items !== null && items.length === 0 && (
        <EmptyState
          icon="upload"
          title={t("sport.empty.title")}
          action={
            <Button variant="primary" icon="upload" onClick={() => input.current?.click()}>
              {t("sport.import.button")}
            </Button>
          }
        >
          <p>{t("sport.empty.body")}</p>
          <p>{t("sport.import.hint")}</p>
        </EmptyState>
      )}
      {items && items.length > 0 && (
        <ul className="sport-list">
          {items.map((a) => (
            <ActivityCard key={a.id} a={a} />
          ))}
        </ul>
      )}
      {cursor && (
        <div className="sport-more">
          <Button busy={loadingMore} onClick={() => void more()}>
            {t("sport.list.more")}
          </Button>
        </div>
      )}

      {results && (
        <Dialog
          title={t("sport.import.title")}
          onClose={() => setResults(null)}
          actions={
            <Button variant="primary" data-autofocus onClick={() => setResults(null)}>
              {t("common.close")}
            </Button>
          }
        >
          <p>
            {[
              t("sport.import.new", { n: n("new") }),
              n("duplicate") ? t("sport.import.duplicate", { n: n("duplicate") }) : null,
              n("error") ? t("sport.import.error", { n: n("error") }) : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <ul className="sport-results">
            {results.map((r, i) => (
              <li key={`${r.name}-${i}`} className={`sport-result-${r.status}`}>
                <span>{r.id && r.status !== "error" ? <Link to={`/sport/${r.id}`}>{r.name}</Link> : r.name}</span>
                <span className="muted">
                  {r.status === "new"
                    ? t("sport.import.statusNew")
                    : r.status === "duplicate"
                      ? t("sport.import.statusDuplicate")
                      : `${t("sport.import.statusError")}${r.reason ? `: ${r.reason}` : ""}`}
                </span>
              </li>
            ))}
          </ul>
        </Dialog>
      )}
      {settingsOpen && config && (
        <SettingsDialog
          config={config}
          onClose={() => setSettingsOpen(false)}
          onSaved={(c) => {
            setConfig(c);
            setSettingsOpen(false);
          }}
        />
      )}
    </div>
  );
}
