// /beheer/taken: hoeveel taken er wachten, lopen en mislukten (ook per soort), wat nu loopt en
// de mislukte taken in pagina's van 100, met Opnieuw en Weggooien. Shift+R: alles opnieuw.

import { type JSX, useState } from "react";
import { api } from "../api";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { useShortcuts } from "../ui/keys";
import { toast } from "../ui/toast";
import { adminApi, type FailedJob, type JobsPage } from "./api";
import { ago, jobSubject, jobTypeLabel } from "./format";
import { usePolling } from "./usePolling";

const STATUSES = ["queued", "running", "done", "failed"] as const;

/** De tellingen per soort als rijen: { type, queued, running, done, failed }. */
export function byTypeRows(page: Pick<JobsPage, "by_type">): { type: string; counts: Record<string, number> }[] {
  const rows = new Map<string, Record<string, number>>();
  for (const x of page.by_type) {
    const row = rows.get(x.type) ?? {};
    row[x.status] = (row[x.status] ?? 0) + x.count;
    rows.set(x.type, row);
  }
  return [...rows].sort(([a], [b]) => a.localeCompare(b)).map(([type, counts]) => ({ type, counts }));
}

export function Jobs(): JSX.Element {
  const jobs = usePolling(() => adminApi.jobs(), 10_000);
  // Oudere pagina's met mislukte taken (na "Meer"); de eerste komt uit de polling
  const [older, setOlder] = useState<FailedJob[]>([]);
  const [next, setNext] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const page = jobs.data;
  const failedCount = page?.counts.failed ?? 0;

  async function act(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    try {
      await fn();
      setError(null);
      setOlder([]);
      setNext(undefined);
      await jobs.reload();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function retryAll() {
    if (failedCount === 0 || busy) return;
    const yes = await confirm({
      title: t("admin.jobs.retryAllTitle"),
      message: t("admin.jobs.retryAllText", { n: failedCount }),
      confirm: t("admin.jobs.retryAll"),
    });
    if (!yes) return;
    let n = 0;
    if (
      await act(async () => {
        n = await api.retryFailedJobs();
      })
    )
      toast(t("admin.jobs.retried", { n }), { kind: "ok" });
  }

  async function remove(job: FailedJob) {
    const yes = await confirm({
      title: t("admin.jobs.removeTitle"),
      message: t("admin.jobs.removeText"),
      confirm: t("admin.jobs.remove"),
      danger: true,
    });
    if (yes) await act(() => adminApi.deleteJob(job.id));
  }

  async function more() {
    const before = next === undefined ? page?.next_before : next;
    if (!before) return;
    setBusy(true);
    try {
      const p = await adminApi.jobs(before);
      setOlder((o) => [...o, ...p.failed]);
      setNext(p.next_before);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  useShortcuts(
    [{ combo: "shift+r", label: t("admin.keys.retryAll"), run: () => void retryAll(), when: () => failedCount > 0 }],
    { scope: "admin" },
  );

  const failed = [...(page?.failed ?? []), ...older.filter((o) => !page?.failed.some((f) => f.id === o.id))];
  const nextBefore = next === undefined ? page?.next_before : next;

  return (
    <section aria-labelledby="admin-jobs-title">
      <h2 id="admin-jobs-title">{t("admin.jobs.title")}</h2>
      {jobs.error !== null && <p role="alert">{t("admin.refreshFailed", { error: errorText(jobs.error) })}</p>}
      {page && (
        <>
          <p>{t("admin.jobs.counts", page.counts)}</p>
          {page.oldest_queued_at && (
            <p className="muted">{t("admin.jobs.oldest", { ago: ago(page.oldest_queued_at) })}</p>
          )}

          {page.by_type.length > 0 && (
            <section aria-labelledby="admin-jobs-types">
              <h3 id="admin-jobs-types">{t("admin.jobs.byType")}</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("admin.jobs.type")}</th>
                      {STATUSES.map((s) => (
                        <th key={s}>{t(`admin.jobs.${s}`)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {byTypeRows(page).map((row) => (
                      <tr key={row.type}>
                        <td>{jobTypeLabel(row.type)}</td>
                        {STATUSES.map((s) => (
                          <td key={s}>{fmt.number(row.counts[s] ?? 0)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section aria-labelledby="admin-jobs-running">
            <h3 id="admin-jobs-running">{t("admin.jobs.runningNow")}</h3>
            {page.running.length === 0 ? (
              <p className="muted">{t("admin.jobs.noneRunning")}</p>
            ) : (
              <ul className="admin-running">
                {page.running.map((j) => (
                  <li key={j.id}>
                    {jobTypeLabel(j.type)} <code>{jobSubject(j.key)}</code>{" "}
                    {j.started_at && (
                      <span className="muted">
                        {t("admin.jobs.started")} {ago(j.started_at)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="admin-jobs-failed">
            <div className="admin-head">
              <h3 id="admin-jobs-failed">{t("admin.jobs.failed")}</h3>
              {failedCount > 0 && (
                <Button aria-keyshortcuts="Shift+R" disabled={busy} onClick={() => void retryAll()}>
                  {t("admin.jobs.retryAll")}
                </Button>
              )}
            </div>
            {failed.length === 0 ? (
              <p className="muted">{t("admin.jobs.noFailed")}</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("admin.jobs.type")}</th>
                      <th>{t("admin.jobs.subject")}</th>
                      <th>{t("admin.jobs.attempts")}</th>
                      <th>{t("admin.jobs.error")}</th>
                      <th className="wide-only">{t("admin.jobs.failedAt")}</th>
                      <th>
                        <span className="sr-only">{t("admin.jobs.retry")}</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {failed.map((j) => (
                      <tr key={j.id}>
                        <td>{jobTypeLabel(j.type)}</td>
                        <td>
                          <code>{jobSubject(j.key)}</code>
                        </td>
                        <td>{j.attempts}</td>
                        <td className="wrap">{j.error}</td>
                        <td className="wide-only">{j.finished_at ? fmt.dateTime(j.finished_at) : ""}</td>
                        <td className="actions">
                          <Button disabled={busy} onClick={() => void act(() => api.retryJob(j.id))}>
                            {t("admin.jobs.retry")}
                          </Button>
                          <Button disabled={busy} onClick={() => void remove(j)}>
                            {t("admin.jobs.remove")}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {nextBefore && (
              <Button disabled={busy} onClick={() => void more()}>
                {t("admin.jobs.more")}
              </Button>
            )}
          </section>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
