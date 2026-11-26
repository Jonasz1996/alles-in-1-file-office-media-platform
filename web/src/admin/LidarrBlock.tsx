// Lidarr op /beheer → Media (fixlijst 26-11, punt 8): muziek en albums. Lidarr zelf (poort 8686)
// is waar je artiesten volgt; hier staan het adres, de API-sleutel en de muziekbibliotheek waar een
// klaar album heen gaat. Het koppelen aan Prowlarr en qBittorrent doet de app zelf
// (api/app/arr/lidarr.py). De API (routers/admin_arr.py) geeft nooit de sleutel terug.
import { type FormEvent, type JSX, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { errorText, t } from "../i18n";
import type { Library } from "../media/types";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";

const BASE = "/api/v1/admin/arr";

interface LidarrView {
  lidarr?: { url: string | null; key_set: boolean; library: string | null };
}

export default function LidarrBlock({ libraries }: { libraries: Library[] }): JSX.Element {
  const music = libraries.filter((l) => l.kind === "music");
  const [view, setView] = useState<LidarrView["lidarr"] | null>(null);
  const [url, setUrl] = useState("http://lidarr:8686");
  const [key, setKey] = useState("");
  const [library, setLibrary] = useState("");
  const [version, setVersion] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const v = (await api.request<LidarrView>("GET", BASE)).lidarr ?? null;
    setView(v);
    if (v?.url) setUrl(v.url);
    setLibrary(v?.library ?? "");
  }, []);

  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
  }, [reload]);

  async function run(name: string, fn: () => Promise<string>): Promise<void> {
    setBusy(name);
    try {
      toast(await fn(), { kind: "ok" });
      setError(null);
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  function save(e: FormEvent) {
    e.preventDefault();
    void run("save", async () => {
      const r = await api.request<{ version: string }>("PUT", `${BASE}/lidarr`, {
        url: url.trim(),
        key: key.trim() || null,
        library: library || null,
      });
      setKey("");
      setVersion(r.version);
      return t("admin.media.arr.saved");
    });
  }

  return (
    <section aria-labelledby="admin-media-lidarr">
      <div className="admin-head">
        <h3 id="admin-media-lidarr">
          {t("admin.media.lidarr.title")}: {view?.key_set ? t("admin.media.arr.set") : t("admin.media.arr.notSet")}
        </h3>
      </div>
      <p className="muted">{t("admin.media.lidarr.intro")}</p>
      <form className="stack" onSubmit={save} data-arr="lidarr">
        <Field label={t("admin.media.arr.url")}>
          {(c) => <input {...c} name="lidarr-url" value={url} onChange={(e) => setUrl(e.target.value)} />}
        </Field>
        <Field label={t("admin.media.arr.key")} hint={t("admin.media.lidarr.keyHelp")}>
          {(c) => (
            <input
              {...c}
              type="password"
              name="lidarr-key"
              autoComplete="off"
              spellCheck={false}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("admin.media.lidarr.library")} hint={t("admin.media.lidarr.libraryHelp")}>
          {(c) => (
            <select {...c} name="lidarr-library" value={library} onChange={(e) => setLibrary(e.target.value)}>
              <option value="">{t("admin.media.lidarr.oldest")}</option>
              {music.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        {music.length === 0 && <p className="muted">{t("admin.media.lidarr.noLibrary")}</p>}
        <div className="toolbar">
          <Button type="submit" variant="primary" busy={busy === "save"}>
            {t("admin.media.arr.save")}
          </Button>
          {view?.key_set && (
            <>
              <Button
                busy={busy === "test"}
                onClick={() =>
                  void run("test", async () => {
                    const r = await api.request<{ version: string }>("POST", `${BASE}/lidarr/test`);
                    setVersion(r.version);
                    return t("admin.media.arr.tested");
                  })
                }
              >
                {t("admin.media.arr.test")}
              </Button>
              <Button
                variant="danger"
                busy={busy === "remove"}
                onClick={() =>
                  void run("remove", async () => {
                    await api.request<void>("DELETE", `${BASE}/lidarr`);
                    setVersion(null);
                    return t("admin.media.arr.removed");
                  })
                }
              >
                {t("admin.media.arr.remove")}
              </Button>
            </>
          )}
        </div>
      </form>
      {version && <p data-arr="lidarr-version">{t("admin.media.lidarr.version", { version })}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
