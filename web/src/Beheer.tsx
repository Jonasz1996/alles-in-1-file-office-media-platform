import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  api,
  formatBytes,
  formatDate,
  type HttpsStatus,
  type SecurityConfig,
  type SystemStatus,
  type User,
} from "./api";

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

export function Beheer({ currentUser }: { currentUser: User }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [https, setHttps] = useState<HttpsStatus | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, u, h] = await Promise.all([api.adminStatus(), api.users(), api.httpsStatus()]);
      setStatus(s);
      setUsers(u);
      setHttps(h);
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

      {https && <Https status={https} onChange={setHttps} />}

      <Security />

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
                <th>2FA</th>
                <th />
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
                  <td>
                    {u.totp_enabled ? "aan" : "uit"}
                    {u.totp_locked_until && (
                      <>
                        {" "}
                        <span className="badge down">geblokkeerd tot {formatDate(u.totp_locked_until)}</span>
                      </>
                    )}
                    {u.totp_enabled && u.id !== currentUser.id && (
                      <>
                        {" "}
                        <button
                          type="button"
                          onClick={async () => {
                            if (!window.confirm(`2FA uitzetten voor ${u.display_name}?`)) return;
                            try {
                              await api.resetTotp(u.id);
                              await load();
                            } catch (e) {
                              setError((e as Error).message);
                            }
                          }}
                        >
                          Uitzetten
                        </button>
                      </>
                    )}
                  </td>
                  <td className="actions">{u.id !== currentUser.id && <SetPassword user={u} />}</td>
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

/** Nieuw wachtwoord voor iemand die het zijne kwijt is; die gebruiker is daarna overal uitgelogd. */
function SetPassword({ user }: { user: User }) {
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  async function set() {
    const password = window.prompt(
      `Nieuw wachtwoord voor ${user.display_name} (minstens 10 tekens). ` +
        "Daarna is die gebruiker op alle apparaten uitgelogd.",
    );
    if (password === null) return;
    if (password.length < 10) {
      setMessage({ text: "Minstens 10 tekens", ok: false });
      return;
    }
    try {
      await api.setUserPassword(user.id, password);
      setMessage({ text: "Ingesteld", ok: true });
    } catch (e) {
      setMessage({ text: (e as Error).message, ok: false });
    }
  }

  return (
    <>
      <button type="button" onClick={set}>
        Wachtwoord instellen
      </button>
      {message &&
        (message.ok ? <span className="badge ok">{message.text}</span> : <span role="alert">{message.text}</span>)}
    </>
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

function Security() {
  const [config, setConfig] = useState<SecurityConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .security()
      .then(setConfig)
      .catch((e: Error) => setError(e.message));
  }, []);

  async function change(require_2fa: boolean) {
    setSaving(true); // één wijziging tegelijk, anders kan een oud antwoord het laatste woord krijgen
    try {
      setConfig(await api.setSecurity({ require_2fa }));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <h2>Beveiliging</h2>
      {config && (
        <div className="stack">
          <label className="check">
            <input
              type="checkbox"
              checked={config.require_2fa}
              disabled={saving}
              onChange={(e) => change(e.target.checked)}
            />
            2FA verplicht voor iedereen
          </label>
          <p className="muted">
            Wie nog geen 2FA heeft, moet het na het inloggen eerst aanzetten en kan tot dan niets anders.
          </p>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

const HTTPS_STATE: Record<HttpsStatus["state"], { label: string; badge: string }> = {
  uit: { label: "Uit: alleen http", badge: "degraded" },
  bezig: { label: "Certificaat wordt aangevraagd…", badge: "degraded" },
  actief: { label: "Actief", badge: "ok" },
  fout: { label: "Aanvragen mislukt", badge: "down" },
};

function Https({ status, onChange }: { status: HttpsStatus; onChange: (s: HttpsStatus) => void }) {
  const [form, setForm] = useState({ domain: status.domain, email: status.email, staging: status.staging });
  const [editing, setEditing] = useState(!status.domain);
  const [error, setError] = useState<string | null>(null);
  const state = HTTPS_STATE[status.state];

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      onChange(await api.httpsConfigure(form));
      setEditing(false);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function retry() {
    try {
      onChange(await api.httpsRenew());
      setError(null);
    } catch (err) {
      setError((err as Error).message); // bijv. te snel na de vorige poging (429)
    }
  }

  return (
    <section>
      <h2>
        HTTPS: <span className={`badge ${state.badge}`}>{state.label}</span>
      </h2>
      {status.domain && (
        <p>
          Domein <strong>{status.domain}</strong>
          {status.staging && " (testmodus: browsers vertrouwen dit certificaat niet)"}
          {status.active && status.expires_at && <>; certificaat geldig tot {formatDate(status.expires_at)}</>}
          {status.active && status.renew_at && <>, wordt vanaf {formatDate(status.renew_at)} vanzelf vernieuwd</>}.
          {status.active && !status.serving && !status.listener_error && " De https-poort start zo."}
        </p>
      )}
      {status.error && <p role="alert">{status.error}</p>}
      {status.listener_error && <p role="alert">{status.listener_error}</p>}
      {status.state === "fout" && status.next_check && (
        <p className="muted">Volgende poging om {formatDate(status.next_check)}.</p>
      )}
      {editing ? (
        <form className="stack" onSubmit={save}>
          <p className="muted">
            Laat het domein (bijv. een A-record) naar je publieke IP-adres wijzen en stuur in je router poort 80 en
            443 door naar deze container. Daarna vraagt het platform zelf een certificaat aan bij Let&apos;s Encrypt
            en vernieuwt het op tijd. Leeg laten = alleen http in je LAN.
          </p>
          <label>
            Domein
            <input
              placeholder="app.voorbeeld.nl"
              value={form.domain}
              onChange={(e) => setForm({ ...form, domain: e.target.value })}
            />
          </label>
          <label>
            E-mailadres voor Let&apos;s Encrypt (optioneel)
            <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.staging}
              onChange={(e) => setForm({ ...form, staging: e.target.checked })}
            />
            Testmodus: eerst proberen bij de test-omgeving van Let&apos;s Encrypt
          </label>
          <div className="toolbar">
            <button type="submit">Opslaan</button>
            {status.domain && (
              <button type="button" onClick={() => setEditing(false)}>
                Annuleren
              </button>
            )}
          </div>
          {error && <p role="alert">{error}</p>}
        </form>
      ) : (
        <div className="toolbar">
          <button type="button" onClick={() => setEditing(true)}>
            Wijzigen
          </button>
          {status.state === "fout" && (
            <button type="button" onClick={retry}>
              Nu opnieuw proberen
            </button>
          )}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
    </section>
  );
}
