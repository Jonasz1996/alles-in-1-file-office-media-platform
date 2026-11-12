// /beheer/gebruikers: de lijst met zoeken, het gebruik per gebruiker en de status. N maakt een
// nieuwe gebruiker, / zet de focus in het zoekveld; Wijzigen opent UserDialog.

import { type JSX, useMemo, useRef, useState } from "react";
import type { User } from "../api";
import { errorText, fmt, fold, t } from "../i18n";
import { Button } from "../ui/Button";
import { useShortcuts } from "../ui/keys";
import { usageLevel } from "../ui/UsageBar";
import { type AdminUser, adminApi } from "./api";
import { matchesUser } from "./format";
import { UserDialog } from "./UserDialog";
import { usePolling } from "./usePolling";

type Open = { mode: "new" } | { mode: "edit"; id: string } | null;

export function Users({ currentUser }: { currentUser: User }): JSX.Element {
  const users = usePolling(adminApi.users, 15_000);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Open>(null);
  const search = useRef<HTMLInputElement>(null);

  useShortcuts(
    [
      { combo: "n", label: t("admin.keys.newUser"), run: () => setOpen({ mode: "new" }) },
      { combo: "/", label: t("admin.keys.findUser"), run: () => search.current?.focus() },
    ],
    { scope: "admin" },
  );

  const list = users.data;
  const shown = useMemo(() => (list ?? []).filter((u) => matchesUser(u, query, fold)), [list, query]);
  const editing = open?.mode === "edit" ? (list?.find((u) => u.id === open.id) ?? null) : null;

  return (
    <section aria-labelledby="admin-users-title">
      <div className="admin-head">
        <h2 id="admin-users-title">{t("admin.users.title")}</h2>
        <Button variant="primary" icon="user" aria-keyshortcuts="n" onClick={() => setOpen({ mode: "new" })}>
          {t("admin.users.add")}
        </Button>
      </div>
      {users.error !== null && <p role="alert">{t("admin.refreshFailed", { error: errorText(users.error) })}</p>}
      <div className="toolbar">
        <input
          ref={search}
          type="search"
          className="admin-search"
          aria-label={t("admin.users.filter")}
          aria-keyshortcuts="/"
          placeholder={t("admin.users.filter")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && query) {
              e.stopPropagation();
              setQuery("");
            }
          }}
        />
        {list && (
          <span className="muted" aria-live="polite">
            {t("admin.users.count", { n: shown.length })}
          </span>
        )}
      </div>
      {list && shown.length === 0 && <p className="muted">{t("admin.users.none")}</p>}
      {shown.length > 0 && (
        <div className="table-wrap">
          <table className="admin-users">
            <thead>
              <tr>
                <th>{t("admin.users.name")}</th>
                <th className="wide-only">{t("admin.users.email")}</th>
                <th>{t("admin.users.role")}</th>
                <th>{t("admin.users.usage")}</th>
                <th className="wide-only">{t("admin.users.twoFactor")}</th>
                <th>{t("admin.users.state")}</th>
                <th>
                  <span className="sr-only">{t("admin.users.edit")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((u) => (
                <Row key={u.id} user={u} me={u.id === currentUser.id} onEdit={() => setOpen({ mode: "edit", id: u.id })} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open?.mode === "new" && (
        <UserDialog
          user={null}
          currentUser={currentUser}
          onClose={() => setOpen(null)}
          onChanged={() => void users.reload()}
        />
      )}
      {editing && (
        <UserDialog
          user={editing}
          currentUser={currentUser}
          onClose={() => setOpen(null)}
          onChanged={() => void users.reload()}
        />
      )}
    </section>
  );
}

function Row({ user: u, me, onEdit }: { user: AdminUser; me: boolean; onEdit(): void }): JSX.Element {
  const used = u.usage
    ? u.usage.files_bytes + u.usage.trash_bytes + u.usage.versions_bytes
    : u.used_bytes;
  const quota = u.quota_bytes > 0 ? u.quota_bytes : null;
  const level = usageLevel(used, quota);
  return (
    <tr className={u.disabled ? "admin-user-blocked" : undefined}>
      <td>
        {u.display_name}
        {me && <span className="muted"> ({t("admin.users.you")})</span>}
      </td>
      <td className="wide-only">{u.email}</td>
      <td>{t(`admin.roles.${u.role}`)}</td>
      <td className={`admin-usage ${level}`}>
        {quota
          ? t("common.usage.ofTotal", { used: fmt.bytes(used), total: fmt.bytes(quota) })
          : `${fmt.bytes(used)} (${t("admin.users.unlimited")})`}
      </td>
      <td className="wide-only">
        {u.totp_enabled ? t("admin.users.on") : t("admin.users.off")}
        {u.totp_locked_until && (
          <>
            {" "}
            <span className="badge down">{t("admin.users.lockedUntil", { date: fmt.dateTime(u.totp_locked_until) })}</span>
          </>
        )}
      </td>
      <td>
        {u.deleting ? (
          <span className="badge degraded">{t("admin.users.deleting")}</span>
        ) : u.disabled ? (
          <span className="badge down">{t("admin.users.blocked")}</span>
        ) : (
          <span className="badge ok">{t("admin.users.active")}</span>
        )}
      </td>
      <td className="actions">
        <Button onClick={onEdit} aria-label={t("admin.users.editLabel", { name: u.display_name })}>
          {t("admin.users.edit")}
        </Button>
      </td>
    </tr>
  );
}
