import { useCallback, useEffect, useState } from "react";
import { api, formatDate, type JobsOverview } from "./api";

const TYPE_LABELS: Record<string, string> = { preview: "Preview" };

/** Waar de taak over gaat, kort: bij een preview de eerste tekens van de inhoud (sha). */
function subject(key: string | null): string {
  if (!key) return "";
  const [, rest = key] = key.split(":", 2);
  return rest.length > 12 ? `${rest.slice(0, 12)}…` : rest;
}

/** Achtergrondtaken op /beheer: hoeveel er wachten en lopen, en wat mislukte. */
export function BeheerJobs() {
  const [overview, setOverview] = useState<JobsOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setOverview(await api.adminJobs());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 10_000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const counts = overview?.counts;
  return (
    <section>
      <h2>Achtergrondtaken</h2>
      {counts && (
        <p>
          {counts.queued} in de wachtrij, {counts.running} bezig, {counts.done} klaar (afgelopen week),{" "}
          <span className={counts.failed ? "badge down" : undefined}>{counts.failed} mislukt</span>.
        </p>
      )}
      {overview && overview.failed.length === 0 && <p className="muted">Geen mislukte taken.</p>}
      {overview && overview.failed.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Soort</th>
                  <th>Waarvoor</th>
                  <th>Pogingen</th>
                  <th>Fout</th>
                  <th>Mislukt op</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {overview.failed.map((job) => (
                  <tr key={job.id}>
                    <td>{TYPE_LABELS[job.type] ?? job.type}</td>
                    <td>
                      <code title={job.key ?? undefined}>{subject(job.key)}</code>
                    </td>
                    <td>{job.attempts}</td>
                    <td className="wrap">{job.error}</td>
                    <td>{job.finished_at ? formatDate(job.finished_at) : ""}</td>
                    <td className="actions">
                      <button type="button" disabled={busy} onClick={() => act(() => api.retryJob(job.id))}>
                        Opnieuw
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="toolbar">
            <button type="button" disabled={busy} onClick={() => act(api.retryFailedJobs)}>
              Alles opnieuw
            </button>
          </div>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
