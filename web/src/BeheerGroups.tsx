// /beheer/groepen: groepen maken, hernoemen, verwijderen en de leden kiezen. Delen met een groep
// geldt meteen voor wie er nu in zit. N zet de focus in het veld voor een nieuwe groep.

import { type FormEvent, type JSX, useCallback, useEffect, useRef, useState } from "react";
import { adminApi, type AdminUser } from "./admin/api";
import { api, type Group } from "./api";
import { collator, errorText, t } from "./i18n";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { confirm, prompt } from "./ui/dialogs";
import { useShortcuts } from "./ui/keys";

const byName = (a: { name: string }, b: { name: string }) => collator.compare(a.name, b.name);

export function BeheerGroups(): JSX.Element {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [name, setName] = useState("");
  const [members, setMembers] = useState<Group | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [g, u] = await Promise.all([api.groups(), adminApi.users()]);
      setGroups(g.sort(byName));
      setUsers(u);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useShortcuts([{ combo: "n", label: t("admin.keys.newGroup"), run: () => input.current?.focus() }], {
    scope: "admin",
  });

  async function act(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    try {
      await fn();
      setError(null);
      await load();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    if (name.trim() && (await act(() => api.createGroup(name.trim())))) setName("");
  }

  async function rename(group: Group) {
    const newName = (
      await prompt({
        title: t("admin.groups.renameTitle"),
        label: t("admin.groups.renameLabel"),
        initial: group.name,
        confirm: t("common.save"),
      })
    )?.trim();
    if (newName && newName !== group.name) await act(() => api.renameGroup(group.id, newName));
  }

  async function remove(group: Group) {
    const yes = await confirm({
      title: t("admin.groups.removeTitle", { name: group.name }),
      message: t("admin.groups.removeText"),
      confirm: t("admin.groups.remove"),
      danger: true,
    });
    if (yes) await act(() => api.deleteGroup(group.id));
  }

  return (
    <section aria-labelledby="admin-groups-title">
      <h2 id="admin-groups-title">{t("admin.groups.title")}</h2>
      <p className="muted">{t("admin.groups.help")}</p>
      {groups?.length === 0 && <p className="muted">{t("admin.groups.none")}</p>}
      {groups && groups.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("admin.groups.name")}</th>
                <th>{t("admin.groups.members")}</th>
                <th>
                  <span className="sr-only">{t("admin.groups.editMembers")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g.id}>
                  <td>{g.name}</td>
                  <td className="wrap">
                    {t("admin.groups.memberCount", { n: g.member_count })}
                    {g.members.length > 0 && (
                      <span className="muted">: {g.members.map((m) => m.display_name).join(", ")}</span>
                    )}
                  </td>
                  <td className="actions">
                    <Button disabled={busy} onClick={() => setMembers(g)}>
                      {t("admin.groups.editMembers")}
                    </Button>
                    <Button disabled={busy} onClick={() => void rename(g)}>
                      {t("admin.groups.rename")}
                    </Button>
                    <Button disabled={busy} onClick={() => void remove(g)}>
                      {t("admin.groups.remove")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form className="inline-form" onSubmit={create}>
        <input
          ref={input}
          aria-label={t("admin.groups.newName")}
          aria-keyshortcuts="n"
          placeholder={t("admin.groups.newPlaceholder")}
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Button type="submit" disabled={busy || !name.trim()}>
          {t("admin.groups.create")}
        </Button>
      </form>
      {error && <p role="alert">{error}</p>}
      {members && (
        <GroupMembers
          group={members}
          users={users}
          onClose={() => setMembers(null)}
          onSave={async (ids) => {
            if (await act(() => api.setGroupMembers(members.id, ids))) setMembers(null);
          }}
          busy={busy}
          error={error}
        />
      )}
    </section>
  );
}

function GroupMembers({
  group,
  users,
  onClose,
  onSave,
  busy,
  error,
}: {
  group: Group;
  users: AdminUser[];
  onClose: () => void;
  onSave: (userIds: string[]) => void;
  busy: boolean;
  error: string | null;
}): JSX.Element {
  const [chosen, setChosen] = useState(() => new Set(group.members.map((m) => m.id)));
  const sorted = [...users].sort((a, b) => collator.compare(a.display_name, b.display_name));

  function toggle(id: string, on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <Dialog
      title={t("admin.groups.membersTitle", { name: group.name })}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={busy} onClick={() => onSave([...chosen])}>
            {t("common.save")}
          </Button>
        </>
      }
    >
      {sorted.length === 0 ? (
        <p className="muted">{t("admin.groups.noUsers")}</p>
      ) : (
        <ul className="admin-members" aria-label={t("admin.groups.usersLabel")}>
          {sorted.map((u, i) => (
            <li key={u.id}>
              <label className="check">
                <input
                  type="checkbox"
                  data-autofocus={i === 0 ? "" : undefined}
                  checked={chosen.has(u.id)}
                  onChange={(e) => toggle(u.id, e.target.checked)}
                />
                {u.display_name} <span className="muted">{u.email}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert">{error}</p>}
    </Dialog>
  );
}
