// Activiteit en statistieken op /beheer → Media (fase 11, stap 7; zoals Tautulli). Wie nu kijkt
// staat al bij de sessies hierboven; dit blok toont wat er laatst gekeken is, wat het meest, en per
// gebruiker hoeveel. De API: GET /api/v1/admin/media/stats?days=N.
import { type JSX, useEffect, useState } from "react";
import { api } from "../api";
import { errorText, fmt, t } from "../i18n";

interface Stats {
  days: number;
  recent: {
    user: string;
    profile: string | null;
    title: string;
    kind: string;
    at: string;
    watched: boolean;
    position_ms: number;
    duration_ms: number;
  }[];
  popular: { node_id: string; title: string; kind: string; plays: number; viewers: number }[];
  users: { user: string; titles: number; watched: number; hours: number }[];
}

const PERIODS = [7, 30, 90, 365] as const;

export default function StatsBlock(): JSX.Element {
  const [days, setDays] = useState<number>(30);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.request<Stats>("GET", `/api/v1/admin/media/stats?days=${days}`).then(
      (s) => live && (setStats(s), setError(null)),
      (e) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [days]);

  return (
    <section aria-labelledby="admin-media-stats">
      <div className="admin-head">
        <h3 id="admin-media-stats">{t("admin.media.stats.title")}</h3>
        <select aria-label={t("admin.media.stats.period")} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {PERIODS.map((d) => (
            <option key={d} value={d}>
              {t("admin.media.stats.days", { n: d })}
            </option>
          ))}
        </select>
      </div>
      {error && <p role="alert">{error}</p>}
      {stats && stats.recent.length === 0 && <p className="muted">{t("admin.media.stats.none")}</p>}
      {stats && stats.users.length > 0 && (
        <table className="admin-table" data-stats="users">
          <caption>{t("admin.media.stats.users")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("admin.media.stats.user")}</th>
              <th scope="col">{t("admin.media.stats.titles")}</th>
              <th scope="col">{t("admin.media.stats.watched")}</th>
              <th scope="col">{t("admin.media.stats.hours")}</th>
            </tr>
          </thead>
          <tbody>
            {stats.users.map((u) => (
              <tr key={u.user}>
                <th scope="row">{u.user}</th>
                <td>{u.titles}</td>
                <td>{u.watched}</td>
                <td>{u.hours}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {stats && stats.popular.length > 0 && (
        <table className="admin-table" data-stats="popular">
          <caption>{t("admin.media.stats.popular")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("admin.media.stats.titleCol")}</th>
              <th scope="col">{t("admin.media.stats.plays")}</th>
              <th scope="col">{t("admin.media.stats.viewers")}</th>
            </tr>
          </thead>
          <tbody>
            {stats.popular.map((p) => (
              <tr key={p.node_id}>
                <th scope="row">{p.title}</th>
                <td>{p.plays}</td>
                <td>{p.viewers}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {stats && stats.recent.length > 0 && (
        <table className="admin-table" data-stats="recent">
          <caption>{t("admin.media.stats.recent")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("admin.media.stats.when")}</th>
              <th scope="col">{t("admin.media.stats.user")}</th>
              <th scope="col">{t("admin.media.stats.titleCol")}</th>
              <th scope="col">{t("admin.media.stats.state")}</th>
            </tr>
          </thead>
          <tbody>
            {stats.recent.map((r, i) => (
              <tr key={`${r.at}-${i}`}>
                <td>{fmt.dateTime(new Date(r.at))}</td>
                <td>{r.profile ? `${r.user} (${r.profile})` : r.user}</td>
                <th scope="row">{r.title}</th>
                <td>
                  {r.watched
                    ? t("admin.media.stats.seen")
                    : t("admin.media.stats.at", {
                        time: fmt.duration(r.position_ms / 1000),
                        total: fmt.duration(r.duration_ms / 1000),
                      })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
