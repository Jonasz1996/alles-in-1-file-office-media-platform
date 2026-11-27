// Verzoeken van gezinsleden op /beheer → Media (fase 11, stap 6): wie vanzelf goedkeuring
// krijgt, en in welke bibliotheek en kwaliteit een goedgekeurd verzoek komt. Goedkeuren zelf
// gebeurt op /media/verzoeken. De API: GET/PUT /api/v1/admin/arr/requests.
import { type JSX, useEffect, useState } from "react";
import { api } from "../api";
import { errorText, t } from "../i18n";
import { Link } from "../links";
import type { Library } from "../media/types";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";
import { type AdminUser, adminApi } from "./api";

const URL = "/api/v1/admin/arr/requests";

interface Conf {
  auto: string[];
  profile: string;
  movies: string | null;
  shows: string | null;
}

export default function RequestsSettings({ libraries }: { libraries: Library[] }): JSX.Element {
  const [conf, setConf] = useState<Conf | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [profiles, setProfiles] = useState<{ key: string; name: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.request<Conf>("GET", URL).then(setConf),
      adminApi.users().then((u) => setUsers(u.filter((x) => x.role === "user" && !x.disabled))),
      api.request<{ key: string; name: string }[]>("GET", "/api/v1/arr/profiles").then(setProfiles),
    ]).catch((e) => setError(errorText(e)));
  }, []);

  async function save() {
    if (!conf) return;
    setBusy(true);
    try {
      setConf(await api.request<Conf>("PUT", URL, conf));
      setError(null);
      toast(t("admin.media.requests.saved"), { kind: "ok" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const pick = (kind: "movies" | "shows") => (
    <Field label={t(`admin.media.requests.${kind}`)}>
      {(c) => (
        <select
          {...c}
          name={`requests-${kind}`}
          value={conf?.[kind] ?? ""}
          onChange={(e) => conf && setConf({ ...conf, [kind]: e.target.value || null })}
        >
          <option value="">{t("admin.media.requests.oldest")}</option>
          {libraries
            .filter((l) => l.kind === kind)
            .map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
        </select>
      )}
    </Field>
  );

  return (
    <section aria-labelledby="admin-media-requests">
      <div className="admin-head">
        <h3 id="admin-media-requests">{t("admin.media.requests.title")}</h3>
      </div>
      <p className="muted">
        {t("admin.media.requests.intro")} <Link to="/media/verzoeken">{t("admin.media.requests.open")}</Link>
      </p>
      {conf && (
        <div className="stack" data-arr="requests-settings">
          <fieldset>
            <legend>{t("admin.media.requests.auto")}</legend>
            {users.length === 0 && <p className="muted">{t("admin.media.requests.noUsers")}</p>}
            {users.map((u) => (
              <label key={u.id} className="block">
                <input
                  type="checkbox"
                  checked={conf.auto.includes(u.id)}
                  onChange={(e) =>
                    setConf({
                      ...conf,
                      auto: e.target.checked ? [...conf.auto, u.id] : conf.auto.filter((x) => x !== u.id),
                    })
                  }
                />{" "}
                {u.display_name}
              </label>
            ))}
          </fieldset>
          {pick("movies")}
          {pick("shows")}
          <Field label={t("admin.media.wanted.profile")}>
            {(c) => (
              <select
                {...c}
                name="requests-profile"
                value={conf.profile}
                onChange={(e) => setConf({ ...conf, profile: e.target.value })}
              >
                {profiles.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <div className="toolbar">
            <Button variant="primary" busy={busy} onClick={() => void save()}>
              {t("admin.media.arr.save")}
            </Button>
          </div>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
