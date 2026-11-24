// Ondertitels van OpenSubtitles op /beheer → Media (fase 11, stap 4). De API
// (routers/admin_arr.py) geeft nooit de sleutel of het wachtwoord terug; leeg laten houdt
// wat er bewaard is. Het zoeken zelf doet app/arr/subs.py op de achtergrond.
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

interface SubsView {
  key_set: boolean;
  username: string | null;
  password_set: boolean;
  languages: string[];
}

export default function SubsBlock(): JSX.Element {
  const [view, setView] = useState<SubsView | null>(null);
  const [key, setKey] = useState("");
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [langs, setLangs] = useState("nl, en");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const v = (await api.request<{ opensubtitles: SubsView }>("GET", BASE))
      .opensubtitles;
    setView(v);
    setUser(v.username ?? "");
    setLangs(v.languages.join(", "));
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

  function remaining(n: number | null): string {
    return n === null
      ? t("admin.media.arr.saved")
      : t("admin.media.subs.remaining", { n });
  }

  function save(e: FormEvent) {
    e.preventDefault();
    void run("save", async () => {
      const r = await api.request<{ remaining: number | null }>(
        "PUT",
        `${BASE}/opensubtitles`,
        {
          key: key.trim() || null,
          username: user.trim() || null,
          password: pass || null,
          languages: langs
            .split(",")
            .map((x) => x.trim().toLowerCase())
            .filter(Boolean),
        },
      );
      setKey("");
      setPass("");
      return remaining(r.remaining);
    });
  }

  return (
    <section aria-labelledby="admin-media-subs">
      <div className="admin-head">
        <h3 id="admin-media-subs">
          {t("admin.media.subs.title")}:{" "}
          {view?.key_set
            ? t("admin.media.arr.set")
            : t("admin.media.arr.notSet")}
        </h3>
      </div>
      <p className="muted">{t("admin.media.subs.intro")}</p>
      <form className="stack" onSubmit={save} data-arr="opensubtitles">
        <Field
          label={t("admin.media.subs.key")}
          hint={t("admin.media.subs.keyHelp")}
        >
          {(c) => (
            <input
              {...c}
              type="password"
              name="os-key"
              autoComplete="off"
              spellCheck={false}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("admin.media.subs.username")}>
          {(c) => (
            <input
              {...c}
              name="os-user"
              autoComplete="off"
              value={user}
              onChange={(e) => setUser(e.target.value)}
            />
          )}
        </Field>
        <Field
          label={t("admin.media.subs.password")}
          hint={t("admin.media.subs.passwordHelp")}
        >
          {(c) => (
            <input
              {...c}
              type="password"
              name="os-password"
              autoComplete="off"
              value={pass}
              onChange={(e) => setPass(e.target.value)}
            />
          )}
        </Field>
        <Field
          label={t("admin.media.subs.languages")}
          hint={t("admin.media.subs.languagesHelp")}
        >
          {(c) => (
            <input
              {...c}
              name="os-languages"
              value={langs}
              onChange={(e) => setLangs(e.target.value)}
            />
          )}
        </Field>
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
                    const r = await api.request<{ remaining: number | null }>(
                      "POST",
                      `${BASE}/opensubtitles/test`,
                    );
                    return remaining(r.remaining);
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
                    await api.request<void>("DELETE", `${BASE}/opensubtitles`);
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
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
