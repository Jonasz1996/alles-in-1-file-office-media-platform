import { type FormEvent, useCallback, useEffect, useState } from "react";
import { type ClusterStatus, fetchClusterStatus, formatBytes, UnauthorizedError } from "./api";

const TOKEN_KEY = "platform.beheertoken";
const CHECK_LABELS: Record<string, string> = {
  etcd: "etcd",
  postgres: "Database",
  seaweed_master: "Opslag (master)",
  seaweed_volume: "Opslag (volume)",
  traefik: "Rand",
  api: "API",
};
const STATUS_LABELS = { ok: "Alles in orde", degraded: "Beperkt", down: "Storing" };

function readToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function Beheer() {
  const [token, setToken] = useState(readToken);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<ClusterStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setStatus(await fetchClusterStatus(token));
      setError(null);
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        setToken("");
        try {
          sessionStorage.removeItem(TOKEN_KEY);
        } catch {
          /* geen opslag beschikbaar */
        }
      }
      setError((e as Error).message);
    }
  }, [token]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  function login(e: FormEvent) {
    e.preventDefault();
    try {
      sessionStorage.setItem(TOKEN_KEY, input);
    } catch {
      /* geen opslag beschikbaar */
    }
    setToken(input);
    setInput("");
  }

  if (!token) {
    return (
      <main>
        <h1>Beheer</h1>
        <form onSubmit={login}>
          <label>
            Beheertoken{" "}
            <input type="password" value={input} onChange={(e) => setInput(e.target.value)} />
          </label>{" "}
          <button type="submit">Inloggen</button>
        </form>
        {error && <p role="alert">{error}</p>}
      </main>
    );
  }

  return (
    <main className="wide">
      <h1>Systeemstatus</h1>
      {error && <p role="alert">Status ophalen mislukt: {error}</p>}
      {status && (
        <>
          <p className={`badge ${status.status}`}>{STATUS_LABELS[status.status]}</p>
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
                  {Object.values(CHECK_LABELS).map((l) => (
                    <th key={l}>{l}</th>
                  ))}
                  <th>Rol database</th>
                  <th>Datadisk</th>
                </tr>
              </thead>
              <tbody>
                {status.nodes.map((n) => (
                  <tr key={n.name}>
                    <td>
                      {n.name}
                      <br />
                      <small>{n.ip}</small>
                    </td>
                    {Object.keys(CHECK_LABELS).map((k) => (
                      <td key={k} className={n.checks[k] ? "up" : "down"}>
                        {n.checks[k] ? "ok" : "uit"}
                      </td>
                    ))}
                    <td>{n.db_role ?? "-"}</td>
                    <td>
                      {n.disk
                        ? `${formatBytes(n.disk.used_bytes)} van ${formatBytes(n.disk.total_bytes)}`
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h2>Database</h2>
          <ul>
            {status.database.map((m) => (
              <li key={m.name}>
                {m.name}: {m.role}, {m.state}
                {m.lag_bytes ? `, ${formatBytes(m.lag_bytes)} achter` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}
