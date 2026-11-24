// Kinderslot per account op /beheer → Media (fase 11, stap 7): een grens op de kijkwijzer die
// voor elk profiel van dat account geldt (bijv. een account voor de kinderen). Profielen binnen
// een account beheert iedereen zelf op /media ("Wie kijkt er?"). De API:
// GET /api/v1/admin/media/profiles en PUT /api/v1/admin/media/profiles/{user_id}.
import { type JSX, useEffect, useState } from "react";
import { api } from "../api";
import { errorText, t } from "../i18n";
import { AGES, ageLabel } from "../media/ProfilePicker";
import { toast } from "../ui/toast";
import { type AdminUser, adminApi } from "./api";

const URL = "/api/v1/admin/media/profiles";

export default function KidsLockBlock(): JSX.Element {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [limits, setLimits] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      adminApi.users().then((u) => setUsers(u.filter((x) => x.role !== "guest" && !x.disabled))),
      api
        .request<{ user_id: string; max_age: number }[]>("GET", URL)
        .then((rows) => setLimits(Object.fromEntries(rows.map((r) => [r.user_id, r.max_age])))),
    ]).catch((e) => setError(errorText(e)));
  }, []);

  async function change(user: AdminUser, value: string) {
    const max_age = value === "" ? null : Number(value);
    try {
      await api.request<void>("PUT", `${URL}/${encodeURIComponent(user.id)}`, { max_age });
      setLimits((old) => {
        const next = { ...old };
        if (max_age === null) delete next[user.id];
        else next[user.id] = max_age;
        return next;
      });
      toast(t("admin.media.kidsLock.saved", { name: user.display_name }), { kind: "ok" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  return (
    <section aria-labelledby="admin-media-kids">
      <div className="admin-head">
        <h3 id="admin-media-kids">{t("admin.media.kidsLock.title")}</h3>
      </div>
      <p className="muted">{t("admin.media.kidsLock.intro")}</p>
      {error && <p role="alert">{error}</p>}
      <table className="admin-table" data-arr="kids-lock">
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <th scope="row">{u.display_name}</th>
              <td>
                <select
                  aria-label={t("admin.media.kidsLock.for", { name: u.display_name })}
                  value={limits[u.id] === undefined ? "" : String(limits[u.id])}
                  onChange={(e) => void change(u, e.target.value)}
                >
                  {AGES.map((a) => (
                    <option key={String(a)} value={a === null ? "" : String(a)}>
                      {ageLabel(a)}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
