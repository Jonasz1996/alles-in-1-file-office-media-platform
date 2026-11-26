// /sport/:id: één activiteit: de cijfers, de kaart, het hoogteprofiel (met de ruwe GPS-hoogte en
// wat het filter herstelde), splits per kilometer, beste tijden en notities. Naam, sport en het
// hoogtefilter zijn te wijzigen; het originele bestand is te downloaden.

import { type JSX, useEffect, useState } from "react";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import { navigate, type RouteProps } from "../router";
import { Button } from "../ui/Button";
import { confirm, prompt } from "../ui/dialogs";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";
import { Spinner } from "../ui/Spinner";
import { toast } from "../ui/toast";
import {
  type Activity,
  type ActivityPatch,
  activity as fetchActivity,
  deleteActivity,
  downloadSource,
  patchActivity,
  SPORTS,
  type SportConfig,
  type SportKind,
  sportConfig,
} from "./api";
import { duration, km, meters, pace, sportLabel, speedKmh, usesPace } from "./format";
import { Profile } from "./Profile";
import { RouteMap } from "./RouteMap";
import "./sport.css";

function Stat({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="sport-stat">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export default function ActivityPage({ params }: RouteProps): JSX.Element {
  const id = params.id;
  const [a, setA] = useState<Activity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<SportConfig | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");

  useEffect(() => {
    setA(null);
    setError(null);
    fetchActivity(id).then(
      (x) => {
        setA(x);
        setNotes(x.notes);
      },
      (e) => setError(errorText(e)),
    );
  }, [id]);

  useEffect(() => {
    sportConfig().then(setConfig, () => setConfig(null));
  }, []);

  async function change(body: ActivityPatch, quiet = false): Promise<void> {
    setBusy(true);
    try {
      const x = await patchActivity(id, body);
      setA(x);
      if (!quiet) toast(t("sport.detail.saved"));
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function rename(): Promise<void> {
    if (!a) return;
    const name = await prompt({
      title: t("sport.detail.rename"),
      label: t("sport.detail.renameLabel"),
      initial: a.name,
      confirm: t("sport.detail.rename"),
      validate: (v) => (v.trim() ? null : t("sport.detail.renameLabel")),
    });
    if (name && name.trim() !== a.name) await change({ name: name.trim() });
  }

  async function remove(): Promise<void> {
    if (!a) return;
    const ok = await confirm({
      title: t("sport.detail.delete"),
      message: t("sport.detail.deleteConfirm", { name: a.name }),
      confirm: t("sport.detail.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteActivity(id);
      toast(t("sport.detail.deleted"));
      navigate("/sport");
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  if (error) {
    return (
      <div className="sport-page">
        <EmptyState icon="info" title={error} action={<Link to="/sport">{t("sport.detail.back")}</Link>} />
      </div>
    );
  }
  if (!a) return <Spinner />;

  const timed = a.moving > 0;
  const paceLike = usesPace(a.sport);
  const efforts = Object.entries(a.efforts).sort((x, y) => Number(x[0]) - Number(y[0]));

  return (
    <div className="sport-page">
      <p className="sport-back">
        <Link to="/sport">{t("sport.detail.back")}</Link>
      </p>
      <PageTitle
        actions={
          <>
            <Button icon="rename" onClick={() => void rename()}>
              {t("sport.detail.rename")}
            </Button>
            <Button icon="download" onClick={() => void downloadSource(a).catch((e) => toast(errorText(e), { kind: "error" }))}>
              {t("sport.detail.download")}
            </Button>
            <Button icon="trash" variant="danger" onClick={() => void remove()}>
              {t("sport.detail.delete")}
            </Button>
          </>
        }
      >
        {a.name}
      </PageTitle>
      <div className="sport-meta">
        {a.start_at && <span>{fmt.dateTime(a.start_at)}</span>}
        <label>
          <span className="sr-only">{t("sport.detail.sport")}</span>
          <select
            value={a.sport}
            disabled={busy}
            onChange={(e) => void change({ sport: e.target.value as SportKind })}
          >
            {SPORTS.map((s) => (
              <option key={s} value={s}>
                {sportLabel(s)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <dl className="sport-stat-grid">
        <Stat label={t("sport.fields.distance")} value={t("sport.units.km", { v: km(a.distance) })} />
        {timed && <Stat label={t("sport.fields.moving")} value={duration(a.moving)} />}
        {timed && <Stat label={t("sport.fields.elapsed")} value={duration(a.elapsed)} />}
        {timed &&
          (paceLike ? (
            <Stat label={t("sport.fields.pace")} value={t("sport.units.pace", { v: pace(a.moving, a.distance) })} />
          ) : (
            <Stat label={t("sport.fields.speed")} value={t("sport.units.kmh", { v: speedKmh(a.distance / a.moving) })} />
          ))}
        {timed && <Stat label={t("sport.fields.maxSpeed")} value={t("sport.units.kmh", { v: speedKmh(a.max_speed) })} />}
        <Stat label={t("sport.fields.gain")} value={t("sport.units.m", { v: meters(a.gain) })} />
        <Stat label={t("sport.fields.loss")} value={t("sport.units.m", { v: meters(a.loss) })} />
        {a.ele_min !== null && a.ele_max !== null && (
          <Stat label={t("sport.fields.eleRange")} value={`${meters(a.ele_min)} – ${t("sport.units.m", { v: meters(a.ele_max) })}`} />
        )}
        {a.avg_hr !== null && <Stat label={t("sport.fields.hr")} value={t("sport.units.bpm", { v: Math.round(a.avg_hr) })} />}
        {a.max_hr !== null && <Stat label={t("sport.fields.maxHr")} value={t("sport.units.bpm", { v: Math.round(a.max_hr) })} />}
        {a.avg_cad !== null && <Stat label={t("sport.fields.cadence")} value={t("sport.units.spm", { v: Math.round(a.avg_cad) })} />}
        {a.avg_power !== null && <Stat label={t("sport.fields.power")} value={t("sport.units.watt", { v: Math.round(a.avg_power) })} />}
      </dl>
      {!timed && <p className="muted">{t("sport.detail.noTime")}</p>}

      <section className="sport-section">
        <h2 className="sr-only">{t("sport.detail.map")}</h2>
        <RouteMap lat={a.series.lat} lon={a.series.lon} bbox={a.bbox} config={config} hover={hover} />
      </section>

      {a.series.ele && (
        <section className="sport-section">
          <h2>{t("sport.detail.profile")}</h2>
          <Profile series={a.series} sport={a.sport} hover={hover} onHover={setHover} />
          <div className="sport-filter">
            <label className="sport-check">
              <input
                type="checkbox"
                checked={a.ele_filter}
                disabled={busy}
                onChange={(e) => void change({ ele_filter: e.target.checked }, true)}
              />
              <span>{t("sport.detail.filterOn")}</span>
            </label>
            <p className="muted">{t("sport.detail.filterHint")}</p>
            {a.ele_filter && a.fixed > 0 && <p>{t("sport.detail.fixed", { n: a.fixed })}</p>}
          </div>
        </section>
      )}

      {a.splits.length > 0 && (
        <section className="sport-section">
          <h2>{t("sport.detail.splits")}</h2>
          <div className="table-wrap">
            <table className="sport-table">
              <thead>
                <tr>
                  <th scope="col">{t("sport.detail.km")}</th>
                  <th scope="col">{paceLike ? t("sport.fields.pace") : t("sport.fields.speed")}</th>
                  <th scope="col">{t("sport.detail.time")}</th>
                  <th scope="col">{t("sport.detail.elev")}</th>
                  {a.splits.some((s) => s.hr !== null) && <th scope="col">{t("sport.fields.hr")}</th>}
                </tr>
              </thead>
              <tbody>
                {a.splits.map((s) => (
                  <tr key={s.km}>
                    <td>{s.distance < 999 ? km(s.distance) : s.km}</td>
                    <td>
                      {paceLike
                        ? pace(s.seconds, s.distance)
                        : s.seconds > 0
                          ? t("sport.units.kmh", { v: speedKmh(s.distance / s.seconds) })
                          : "–"}
                    </td>
                    <td>{duration(s.seconds)}</td>
                    <td>{`${s.elev >= 0 ? "+" : "−"}${meters(Math.abs(s.elev))}`}</td>
                    {a.splits.some((x) => x.hr !== null) && <td>{s.hr !== null ? Math.round(s.hr) : "–"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {efforts.length > 0 && (
        <section className="sport-section">
          <h2>{t("sport.detail.efforts")}</h2>
          <ul className="sport-efforts">
            {efforts.map(([d, s]) => (
              <li key={d}>
                <strong>{t("sport.units.km", { v: km(Number(d), Number(d) % 1000 ? 2 : 0) })}</strong>
                <span>{duration(s)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="sport-section">
        <h2>{t("sport.detail.notes")}</h2>
        <textarea
          className="sport-notes"
          rows={4}
          value={notes}
          aria-label={t("sport.detail.notes")}
          onChange={(e) => setNotes(e.target.value)}
        />
        <div>
          <Button disabled={busy || notes === a.notes} onClick={() => void change({ notes })}>
            {t("sport.detail.notesSave")}
          </Button>
        </div>
        <p className="muted">
          {t("sport.fields.file")}: {a.source_name} · {t("sport.fields.points")}: {fmt.number(a.points)}
        </p>
      </section>
    </div>
  );
}
