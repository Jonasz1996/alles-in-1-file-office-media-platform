// AI-upscaling op /beheer → Media (fase 11, stap 5c): aan of uit, het venster (standaard :20 tot
// :40, want de RTX 3060 Ti is daarbuiten van het AI-verslag en de security-analyse), de grens
// voor het videogeheugen, het token voor de upscaler-container (één keer te zien) en de rij.
// De API staat in api/app/routers/arr_upscale.py; het ontwerp in docs/fase-11/ai-upscaling.md.
import { type JSX, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";

const BASE = "/api/v1/arr/upscale";
const ADMIN = "/api/v1/admin/arr/upscale";

interface View {
  enabled: boolean;
  start: number;
  end: number;
  vram_mb: number;
  token_set: boolean;
  window_open: boolean;
  worker_seen_at: string | null;
}

interface Job {
  id: string;
  name: string;
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  segments_done: number;
  segments_total: number | null;
  reason: string | null;
}

export default function UpscaleBlock(): JSX.Element {
  const [view, setView] = useState<View | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [v, j] = await Promise.all([api.request<View>("GET", ADMIN), api.request<Job[]>("GET", BASE)]);
    setView(v);
    setJobs(j);
  }, []);

  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
  }, [reload]);

  async function run(key: string, fn: () => Promise<string | null>) {
    setBusy(key);
    try {
      const message = await fn();
      setError(null);
      if (message) toast(message, { kind: "ok" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const minute = (key: "start" | "end") => (
    <Field label={t(`admin.media.upscale.${key}`)}>
      {(c) => (
        <input
          {...c}
          type="number"
          min={0}
          max={59}
          name={`upscale-${key}`}
          value={view?.[key] ?? 0}
          onChange={(e) => view && setView({ ...view, [key]: Number(e.target.value) })}
        />
      )}
    </Field>
  );

  function progress(j: Job): string {
    if (j.state === "running" && j.segments_total) {
      return t("admin.media.upscale.running", { pct: Math.floor((j.segments_done / j.segments_total) * 100) });
    }
    return t(`admin.media.upscale.state.${j.state}`);
  }

  return (
    <section aria-labelledby="admin-media-upscale">
      <div className="admin-head">
        <h3 id="admin-media-upscale">{t("admin.media.upscale.title")}</h3>
      </div>
      <p className="muted">{t("admin.media.upscale.intro")}</p>
      {view && (
        <div className="stack" data-arr="upscale">
          <label>
            <input
              type="checkbox"
              checked={view.enabled}
              onChange={(e) => setView({ ...view, enabled: e.target.checked })}
            />{" "}
            {t("admin.media.upscale.enabled")}
          </label>
          {minute("start")}
          {minute("end")}
          <Field label={t("admin.media.upscale.vram")}>
            {(c) => (
              <input
                {...c}
                type="number"
                min={1024}
                max={8192}
                step={512}
                name="upscale-vram"
                value={view.vram_mb}
                onChange={(e) => setView({ ...view, vram_mb: Number(e.target.value) })}
              />
            )}
          </Field>
          <p className="muted">
            {view.window_open ? t("admin.media.upscale.open") : t("admin.media.upscale.closed")}
            {" · "}
            {view.worker_seen_at
              ? t("admin.media.upscale.seen", { when: fmt.dateTime(view.worker_seen_at) })
              : t("admin.media.upscale.neverSeen")}
          </p>
          <div className="toolbar">
            <Button
              variant="primary"
              busy={busy === "save"}
              onClick={() =>
                void run("save", async () => {
                  setView(
                    await api.request<View>("PUT", ADMIN, {
                      enabled: view.enabled,
                      start: view.start,
                      end: view.end,
                      vram_mb: view.vram_mb,
                    }),
                  );
                  return t("admin.media.requests.saved");
                })
              }
            >
              {t("admin.media.arr.save")}
            </Button>
            <Button
              busy={busy === "token"}
              onClick={() =>
                void run("token", async () => {
                  setToken((await api.request<{ token: string }>("POST", `${ADMIN}/token`)).token);
                  await reload();
                  return null;
                })
              }
            >
              {view.token_set ? t("admin.media.upscale.newToken") : t("admin.media.upscale.makeToken")}
            </Button>
          </div>
          {token && (
            <p data-arr="upscale-token">
              {t("admin.media.upscale.tokenOnce")} <code>{token}</code>
            </p>
          )}
        </div>
      )}
      <h4>{t("admin.media.upscale.queue", { n: jobs.length })}</h4>
      {jobs.length > 0 && (
        <ul className="stack" data-arr="upscale-queue">
          {jobs.map((j) => (
            <li key={j.id} data-state={j.state}>
              <strong>{j.name}</strong> · {progress(j)}
              {j.state === "failed" && j.reason ? ` · ${j.reason}` : ""}{" "}
              <Button
                busy={busy === `cancel-${j.id}`}
                onClick={() =>
                  void run(`cancel-${j.id}`, async () => {
                    await api.request<void>("DELETE", `${BASE}/${encodeURIComponent(j.id)}`);
                    await reload();
                    return null;
                  })
                }
              >
                {j.state === "queued" || j.state === "running"
                  ? t("admin.media.upscale.cancel")
                  : t("admin.media.upscale.remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
