// /beheer: de systeemstatus in kaarten (status, versie en uptime, database, wachtrij, https),
// de meldingen en de nodes. Ververst elke 5 seconden zolang het tabblad zichtbaar is.

import type { JSX } from "react";
import { errorText, fmt, type MessageKey, t } from "../i18n";
import { type AdminStatus, adminApi } from "./api";
import { ago, noticeText } from "./format";
import { usePolling } from "./usePolling";

const CHECKS = ["database", "storage", "etcd", "postgres", "seaweed_master", "seaweed_volume", "traefik", "api"];

export function Overview(): JSX.Element {
  const status = usePolling(adminApi.status, 5000);
  const s = status.data;
  return (
    <section aria-labelledby="admin-status-title">
      <h2 id="admin-status-title" className="sr-only">
        {t("admin.status.title")}
      </h2>
      {status.error !== null && (
        <p role="alert">{t("admin.refreshFailed", { error: errorText(status.error) })}</p>
      )}
      {s && <Cards status={s} />}
      {s && <Notices status={s} />}
      {s && <Nodes status={s} />}
    </section>
  );
}

function Cards({ status }: { status: AdminStatus }): JSX.Element {
  const sys = status.system;
  const now = Date.now();
  return (
    <ul className="admin-cards" aria-label={t("admin.overview.cards")}>
      <li className="admin-card">
        <h3>{t("admin.status.title")}</h3>
        <p className={`badge ${status.status}`}>{t(`admin.status.${status.status}`)}</p>
      </li>
      {sys && (
        <li className="admin-card">
          <h3>{t("admin.overview.version")}</h3>
          <p>
            {t("admin.overview.versionText", {
              version: sys.version,
              mode: sys.mode === "cluster" ? t("admin.overview.cluster") : t("admin.overview.standalone"),
            })}
          </p>
          {sys.started_at && (
            <p className="muted">
              {t("admin.overview.since", { date: fmt.dateTime(sys.started_at), ago: ago(sys.started_at, now) })}
            </p>
          )}
        </li>
      )}
      {sys && (
        <li className="admin-card">
          <h3>{t("admin.overview.database")}</h3>
          <p>
            {t("admin.overview.dbSize", {
              kind: t(`admin.overview.${sys.db_kind}` as MessageKey),
              size: fmt.bytes(sys.db_bytes),
            })}
          </p>
        </li>
      )}
      {sys && (
        <li className="admin-card">
          <h3>{t("admin.overview.queue")}</h3>
          <p>
            {t("admin.overview.queueText", {
              queued: sys.jobs.queued,
              running: sys.jobs.running,
              failed: sys.jobs.failed,
            })}
          </p>
          {sys.jobs.oldest_queued_at && (
            <p className="muted">{t("admin.overview.oldest", { ago: ago(sys.jobs.oldest_queued_at, now) })}</p>
          )}
          {!sys.jobs.runner_alive && <p className="badge down">{t("admin.overview.runnerDown")}</p>}
        </li>
      )}
      {sys?.https.state && (
        <li className="admin-card">
          <h3>{t("admin.overview.https")}</h3>
          <p className={`badge ${sys.https.state === "actief" ? "ok" : sys.https.state === "fout" ? "down" : "degraded"}`}>
            {t(`admin.https.state.${sys.https.state}`)}
          </p>
          {sys.https.days_left !== null && (
            <p className="muted">{t("admin.overview.httpsDays", { n: Math.max(0, sys.https.days_left) })}</p>
          )}
        </li>
      )}
    </ul>
  );
}

function Notices({ status }: { status: AdminStatus }): JSX.Element {
  // Oudere servers sturen alleen messages
  const notices = status.notices ?? status.messages.map((text) => ({ code: "", params: {}, text }));
  return (
    <section aria-labelledby="admin-notices">
      <h3 id="admin-notices">{t("admin.overview.notices")}</h3>
      {notices.length === 0 ? (
        <p className="muted">{t("admin.overview.noNotices")}</p>
      ) : (
        <ul className="admin-notices">
          {notices.map((n) => (
            <li key={`${n.code}:${n.text}`}>{noticeText(n)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Nodes({ status }: { status: AdminStatus }): JSX.Element {
  const keys = CHECKS.filter((k) => status.nodes.some((n) => k in n.checks));
  return (
    <section aria-labelledby="admin-nodes">
      <h3 id="admin-nodes">{t("admin.overview.nodes")}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("admin.overview.node")}</th>
              {keys.map((k) => (
                <th key={k}>{t(`admin.checks.${k}` as MessageKey)}</th>
              ))}
              <th>{t("admin.overview.dbRole")}</th>
              <th>{t("admin.overview.disk")}</th>
            </tr>
          </thead>
          <tbody>
            {status.nodes.map((n) => (
              <tr key={n.name}>
                <td>{n.name}</td>
                {keys.map((k) => (
                  <td key={k} className={n.checks[k] ? "up" : "down"}>
                    {n.checks[k] ? t("admin.checks.up") : t("admin.checks.down")}
                  </td>
                ))}
                <td>{n.db_role ?? "–"}</td>
                <td>
                  {n.disk
                    ? t("admin.overview.diskText", { used: fmt.bytes(n.disk.used_bytes), total: fmt.bytes(n.disk.total_bytes) })
                    : "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        {t("admin.overview.counts", {
          users: status.users,
          files: status.files,
          stored: fmt.bytes(status.stored_bytes),
          logical: fmt.bytes(status.logical_bytes),
        })}
      </p>
    </section>
  );
}
