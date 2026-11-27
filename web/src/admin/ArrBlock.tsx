// Mediabeheer op /beheer → Media (fase 11, stap 2c): Prowlarr en qBittorrent koppelen en testen.
// De API (routers/admin_arr.py) geeft nooit een sleutel of wachtwoord terug; de velden daarvoor
// blijven leeg en "leeg laten" houdt wat er bewaard is.
import {
  type FormEvent,
  type JSX,
  useCallback,
  useEffect,
  useState,
} from "react";
import { api } from "../api";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";

const BASE = "/api/v1/admin/arr";

interface ArrView {
  prowlarr: { url: string | null; key_set: boolean };
  qbittorrent: {
    url: string | null;
    username: string | null;
    password_set: boolean;
    category: string;
    save_path: string;
  };
}

interface ProwlarrTest {
  indexers: { id: number; name: string; privacy: string }[];
}

export default function ArrBlock(): JSX.Element {
  const [view, setView] = useState<ArrView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [sites, setSites] = useState<ProwlarrTest["indexers"] | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [pUrl, setPUrl] = useState("");
  const [pKey, setPKey] = useState("");
  const [qUrl, setQUrl] = useState("");
  const [qUser, setQUser] = useState("");
  const [qPass, setQPass] = useState("");
  const [qPath, setQPath] = useState("/downloads");

  const reload = useCallback(async () => {
    const v = await api.request<ArrView>("GET", BASE);
    setView(v);
    setPUrl(v.prowlarr.url ?? "http://prowlarr:9696");
    setQUrl(v.qbittorrent.url ?? "http://qbittorrent:8080");
    setQUser(v.qbittorrent.username ?? "platform");
    setQPath(v.qbittorrent.save_path);
  }, []);

  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
  }, [reload]);

  async function run(key: string, fn: () => Promise<string>): Promise<void> {
    setBusy(key);
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

  function saveProwlarr(e: FormEvent) {
    e.preventDefault();
    void run("p-save", async () => {
      const r = await api.request<ProwlarrTest>("PUT", `${BASE}/prowlarr`, {
        url: pUrl.trim(),
        key: pKey.trim() || null,
      });
      setPKey("");
      setSites(r.indexers);
      return t("admin.media.arr.saved");
    });
  }

  function saveQbit(e: FormEvent) {
    e.preventDefault();
    void run("q-save", async () => {
      const r = await api.request<{ version: string }>(
        "PUT",
        `${BASE}/qbittorrent`,
        {
          url: qUrl.trim(),
          username: qUser.trim(),
          password: qPass || null,
          save_path: qPath.trim(),
        },
      );
      setQPass("");
      setVersion(r.version);
      return t("admin.media.arr.saved");
    });
  }

  return (
    <section aria-labelledby="admin-media-arr">
      <div className="admin-head">
        <h3 id="admin-media-arr">{t("admin.media.arr.title")}</h3>
      </div>
      <p className="muted">{t("admin.media.arr.intro")}</p>

      <h4>
        {t("admin.media.arr.prowlarr")}:{" "}
        {view?.prowlarr.key_set
          ? t("admin.media.arr.set")
          : t("admin.media.arr.notSet")}
      </h4>
      <form className="stack" onSubmit={saveProwlarr} data-arr="prowlarr">
        <Field label={t("admin.media.arr.url")}>
          {(c) => (
            <input
              {...c}
              name="prowlarr-url"
              value={pUrl}
              onChange={(e) => setPUrl(e.target.value)}
            />
          )}
        </Field>
        <Field
          label={t("admin.media.arr.key")}
          hint={t("admin.media.arr.keyHelp")}
        >
          {(c) => (
            <input
              {...c}
              type="password"
              name="prowlarr-key"
              autoComplete="off"
              spellCheck={false}
              value={pKey}
              onChange={(e) => setPKey(e.target.value)}
            />
          )}
        </Field>
        <div className="toolbar">
          <Button type="submit" variant="primary" busy={busy === "p-save"}>
            {t("admin.media.arr.save")}
          </Button>
          {view?.prowlarr.key_set && (
            <>
              <Button
                busy={busy === "p-test"}
                onClick={() =>
                  void run("p-test", async () => {
                    const r = await api.request<ProwlarrTest>(
                      "POST",
                      `${BASE}/prowlarr/test`,
                    );
                    setSites(r.indexers);
                    return t("admin.media.arr.tested");
                  })
                }
              >
                {t("admin.media.arr.test")}
              </Button>
              <Button
                variant="danger"
                busy={busy === "p-remove"}
                onClick={() =>
                  void run("p-remove", async () => {
                    await api.request<void>("DELETE", `${BASE}/prowlarr`);
                    setSites(null);
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
      {sites !== null &&
        (sites.length === 0 ? (
          <p className="muted">{t("admin.media.arr.noSites")}</p>
        ) : (
          <p data-arr="sites">
            {t("admin.media.arr.sites", { n: sites.length })}:{" "}
            {sites.map((s) => s.name).join(", ")}
          </p>
        ))}

      <h4>
        {t("admin.media.arr.qbit")}:{" "}
        {view?.qbittorrent.password_set
          ? t("admin.media.arr.set")
          : t("admin.media.arr.notSet")}
      </h4>
      <form className="stack" onSubmit={saveQbit} data-arr="qbittorrent">
        <Field label={t("admin.media.arr.url")}>
          {(c) => (
            <input
              {...c}
              name="qbit-url"
              value={qUrl}
              onChange={(e) => setQUrl(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("admin.media.arr.username")}>
          {(c) => (
            <input
              {...c}
              name="qbit-user"
              value={qUser}
              onChange={(e) => setQUser(e.target.value)}
            />
          )}
        </Field>
        <Field
          label={t("admin.media.arr.password")}
          hint={t("admin.media.arr.passwordHelp")}
        >
          {(c) => (
            <input
              {...c}
              type="password"
              name="qbit-password"
              autoComplete="off"
              value={qPass}
              onChange={(e) => setQPass(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("admin.media.arr.savePath")}>
          {(c) => (
            <input
              {...c}
              name="qbit-path"
              value={qPath}
              onChange={(e) => setQPath(e.target.value)}
            />
          )}
        </Field>
        <div className="toolbar">
          <Button type="submit" variant="primary" busy={busy === "q-save"}>
            {t("admin.media.arr.save")}
          </Button>
          {view?.qbittorrent.password_set && (
            <>
              <Button
                busy={busy === "q-test"}
                onClick={() =>
                  void run("q-test", async () => {
                    const r = await api.request<{ version: string }>(
                      "POST",
                      `${BASE}/qbittorrent/test`,
                    );
                    setVersion(r.version);
                    return t("admin.media.arr.tested");
                  })
                }
              >
                {t("admin.media.arr.test")}
              </Button>
              <Button
                variant="danger"
                busy={busy === "q-remove"}
                onClick={() =>
                  void run("q-remove", async () => {
                    await api.request<void>("DELETE", `${BASE}/qbittorrent`);
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
      {version && (
        <p data-arr="version">{t("admin.media.arr.version", { version })}</p>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
