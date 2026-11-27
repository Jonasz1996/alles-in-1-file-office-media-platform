// Opruimregels op /beheer → Media (fase 11, stap 8): films en series waar al lang niemand naar
// kijkt, gaan eerst op de lijst "binnenkort weg"; een beheerder kan ze tegenhouden. Daarna gaan
// ze naar de prullenbak (terug te zetten tot die geleegd wordt). De API: /api/v1/admin/arr/cleanup.
import { type JSX, useEffect, useState } from "react";
import { api } from "../api";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";

const URL = "/api/v1/admin/arr/cleanup";

interface Rules {
  movies: boolean;
  movie_days: number;
  shows: boolean;
  show_days: number;
  grace_days: number;
  watched_only: boolean;
}

interface Item {
  id: string;
  kind: "movie" | "show";
  title: string;
  due_at: string;
  kept: boolean;
  done_at: string | null;
}

type View = Rules & { items: Item[] };

export default function CleanupBlock(): JSX.Element {
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .request<View>("GET", URL)
      .then(setView)
      .catch((e) => setError(errorText(e)));
  }, []);

  async function call(key: string, method: "PUT" | "POST", path: string, body?: unknown) {
    setBusy(key);
    try {
      setView(await api.request<View>(method, URL + path, body));
      if (key === "save") toast(t("admin.media.cleanup.saved"), { kind: "ok" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(null);
    }
  }

  const days = (key: "movie_days" | "show_days" | "grace_days", min: number) => (
    <Field label={t(`admin.media.cleanup.${key}`)}>
      {(c) => (
        <input
          {...c}
          type="number"
          min={min}
          max={key === "grace_days" ? 90 : 3650}
          name={`cleanup-${key}`}
          value={view?.[key] ?? 0}
          onChange={(e) => view && setView({ ...view, [key]: Number(e.target.value) })}
        />
      )}
    </Field>
  );

  const check = (key: "movies" | "shows" | "watched_only") => (
    <label>
      <input
        type="checkbox"
        checked={view?.[key] ?? false}
        onChange={(e) => view && setView({ ...view, [key]: e.target.checked })}
      />{" "}
      {t(`admin.media.cleanup.${key}`)}
    </label>
  );

  function state(i: Item): string {
    if (i.done_at) return t("admin.media.cleanup.done", { when: fmt.date(i.done_at) });
    if (i.kept) return t("admin.media.cleanup.kept");
    return t("admin.media.cleanup.due", { when: fmt.date(i.due_at) });
  }

  return (
    <section aria-labelledby="admin-media-cleanup">
      <div className="admin-head">
        <h3 id="admin-media-cleanup">{t("admin.media.cleanup.title")}</h3>
      </div>
      <p className="muted">{t("admin.media.cleanup.intro")}</p>
      {error && <p role="alert">{error}</p>}
      {view && (
        <div className="stack" data-arr="cleanup">
          {check("movies")}
          {days("movie_days", 7)}
          {check("shows")}
          {days("show_days", 7)}
          {check("watched_only")}
          {days("grace_days", 1)}
          <div className="toolbar">
            <Button
              variant="primary"
              busy={busy === "save"}
              onClick={() => {
                const { items: _items, ...rules } = view;
                void call("save", "PUT", "", rules);
              }}
            >
              {t("admin.media.cleanup.save")}
            </Button>
            <Button busy={busy === "run"} onClick={() => void call("run", "POST", "/run")}>
              {t("admin.media.cleanup.run")}
            </Button>
          </div>
          <h4>{t("admin.media.cleanup.list")}</h4>
          {view.items.length === 0 ? (
            <p className="muted">{t("admin.media.cleanup.empty")}</p>
          ) : (
            <table className="admin-table" data-arr="cleanup-list">
              <tbody>
                {view.items.map((i) => (
                  <tr key={i.id}>
                    <th scope="row">{i.title}</th>
                    <td>{t(`admin.media.cleanup.kind.${i.kind}`)}</td>
                    <td>{state(i)}</td>
                    <td>
                      {!i.done_at && (
                        <span className="toolbar">
                          <Button
                            busy={busy === `keep-${i.id}`}
                            onClick={() =>
                              void call(`keep-${i.id}`, "POST", `/${i.id}/keep`, { kept: !i.kept })
                            }
                          >
                            {i.kept ? t("admin.media.cleanup.release") : t("admin.media.cleanup.keep")}
                          </Button>
                          {!i.kept && (
                            <Button
                              busy={busy === `now-${i.id}`}
                              onClick={() => void call(`now-${i.id}`, "POST", `/${i.id}/now`)}
                            >
                              {t("admin.media.cleanup.now")}
                            </Button>
                          )}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
