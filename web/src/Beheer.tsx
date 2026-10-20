import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, formatBytes, type SystemStatus, type User } from "./api";

const CHECK_LABELS: Record<string, string> = {
  database: "Database",
  storage: "Opslag",
  etcd: "etcd",
  postgres: "Database",
  seaweed_master: "Opslag (master)",
  seaweed_volume: "Opslag (volume)",
  traefik: "Rand",
  api: "API",
};
const STATUS_LABELS = { ok: "Alles in orde", degraded: "Beperkt", down: "Storing" };

export function Beheer() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, u] = await Promise.all([api.adminStatus(), api.users()]);
      setStatus(s);
      setUsers(u);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const checkKeys = Object.keys(CHECK_LABELS).filter((k) => status?.nodes.some((n) => k in n.checks));

  return (
    <main className="wide">
      <h1>Beheer</h1>
      {error && <p role="alert">{error}</p>}
      {status && (
        <section>
          <h2>
            Systeemstatus: <span className={`badge ${status.status}`}>{STATUS_LABELS[status.status]}</span>
          </h2>
          {status.messages.length > 0 && (
            <ul>
              {status.messages.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Node</th>
                  {checkKeys.map((k) => (
                    <th key={k}>{CHECK_LABELS[k]}</th>
                  ))}
                  <th>Soort database</th>
                  <th>Schijf</th>
                </tr>
              </thead>
              <tbody>
                {status.nodes.map((n) => (
                  <tr key={n.name}>
                    <td>{n.name}</td>
                    {checkKeys.map((k) => (
                      <td key={k} className={n.checks[k] ? "up" : "down"}>
                        {n.checks[k] ? "ok" : "uit"}
                      </td>
                    ))}
                    <td>{n.db_role ?? "-"}</td>
                    <td>
                      {n.disk ? `${formatBytes(n.disk.used_bytes)} van ${formatBytes(n.disk.total_bytes)}` : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">
            {status.users} gebruikers, {status.files} bestanden. Op schijf: {formatBytes(status.stored_bytes)};
            zonder ontdubbeling zou dat {formatBytes(status.logical_bytes)} zijn.
          </p>
        </section>
      )}

      <section>
        <h2>Gebruikers</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>E-mailadres</th>
                <th>Rol</th>
                <th>Gebruikt</th>
                <th>Quotum</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.display_name}</td>
                  <td>{u.email}</td>
                  <td>{u.role}</td>
                  <td>{formatBytes(u.used_bytes)}</td>
                  <td>{u.quota_bytes ? formatBytes(u.quota_bytes) : "onbeperkt"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <NewUser onCreated={load} />
      </section>
    </main>
  );
}

function NewUser({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState({ email: "", display_name: "", password: "", role: "user", quota_gb: "" });
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.addUser({
        email: form.email,
        display_name: form.display_name,
        password: form.password,
        role: form.role,
        quota_bytes: form.quota_gb ? Math.round(Number(form.quota_gb) * 1024 ** 3) : 0,
      });
      setForm({ email: "", display_name: "", password: "", role: "user", quota_gb: "" });
      setError(null);
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const field = (key: keyof typeof form) => ({
    value: form[key],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value }),
  });

  return (
    <form className="inline-form" onSubmit={submit}>
      <h3>Gebruiker toevoegen</h3>
      <input placeholder="Naam" required {...field("display_name")} />
      <input type="email" placeholder="E-mailadres" required {...field("email")} />
      <input type="password" placeholder="Wachtwoord (min. 10)" minLength={10} required {...field("password")} />
      <select {...field("role")}>
        <option value="user">Gebruiker</option>
        <option value="admin">Beheerder</option>
        <option value="guest">Gast</option>
      </select>
      <input type="number" min="0" step="1" placeholder="Quotum in GB (leeg = onbeperkt)" {...field("quota_gb")} />
      <button type="submit">Toevoegen</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
