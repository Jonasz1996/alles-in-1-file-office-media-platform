import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, type Group, type User } from "./api";
import { Dialog } from "./Dialog";
import { memberCount } from "./sharing";
import "./share.css";

const byName = (a: { name: string }, b: { name: string }) =>
  a.name.localeCompare(b.name, "nl", { sensitivity: "base" });

/** Groepen op /beheer: maken, hernoemen, verwijderen en de leden kiezen. Delen met een groep
 * geldt meteen voor wie er nu in zit. */
export function BeheerGroups({ users }: { users: User[] }) {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [name, setName] = useState("");
  const [members, setMembers] = useState<Group | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setGroups((await api.groups()).sort(byName));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function act(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    try {
      await fn();
      setError(null);
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    if (name.trim() && (await act(() => api.createGroup(name.trim())))) setName("");
  }

  function rename(group: Group) {
    const newName = window.prompt("Nieuwe naam voor de groep", group.name)?.trim();
    if (newName && newName !== group.name) act(() => api.renameGroup(group.id, newName));
  }

  function remove(group: Group) {
    if (
      window.confirm(
        `Groep "${group.name}" verwijderen? Wat met deze groep gedeeld is, is daarna niet meer gedeeld met de leden.`,
      )
    )
      act(() => api.deleteGroup(group.id));
  }

  return (
    <section>
      <h2>Groepen</h2>
      <p className="muted">
        Deel een map met een groep, dan kan iedereen in de groep erbij, ook wie er later bij komt.
      </p>
      {groups?.length === 0 && <p className="muted">Er zijn nog geen groepen.</p>}
      {groups && groups.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Leden</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g.id}>
                  <td>{g.name}</td>
                  <td className="wrap">
                    {memberCount(g.member_count)}
                    {g.members.length > 0 && (
                      <span className="muted">: {g.members.map((m) => m.display_name).join(", ")}</span>
                    )}
                  </td>
                  <td className="actions">
                    <button type="button" disabled={busy} onClick={() => setMembers(g)}>
                      Leden…
                    </button>
                    <button type="button" disabled={busy} onClick={() => rename(g)}>
                      Hernoemen
                    </button>
                    <button type="button" disabled={busy} onClick={() => remove(g)}>
                      Verwijderen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form className="inline-form" onSubmit={create}>
        <input
          aria-label="Naam van een nieuwe groep"
          placeholder="Nieuwe groep, bijv. Familie"
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" disabled={busy || !name.trim()}>
          Groep maken
        </button>
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
  users: User[];
  onClose: () => void;
  onSave: (userIds: string[]) => void;
  busy: boolean;
  error: string | null;
}) {
  const [chosen, setChosen] = useState(() => new Set(group.members.map((m) => m.id)));
  const sorted = [...users].sort((a, b) => a.display_name.localeCompare(b.display_name, "nl", { sensitivity: "base" }));

  function toggle(id: string, on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <Dialog title={`Leden van "${group.name}"`} onClose={onClose}>
      {sorted.length === 0 ? (
        <p className="muted">Er zijn nog geen gebruikers.</p>
      ) : (
        <ul className="tag-list" aria-label="Gebruikers">
          {sorted.map((u) => (
            <li key={u.id}>
              <label>
                <input type="checkbox" checked={chosen.has(u.id)} onChange={(e) => toggle(u.id, e.target.checked)} />
                {u.display_name} <span className="muted">{u.email}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Annuleren
        </button>
        <button type="button" className="primary" disabled={busy} onClick={() => onSave([...chosen])}>
          Opslaan
        </button>
      </div>
    </Dialog>
  );
}
