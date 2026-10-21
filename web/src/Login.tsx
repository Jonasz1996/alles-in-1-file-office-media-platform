import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api, type User } from "./api";

/** message: waarom je (weer) moet inloggen, bijv. "Dit account is uitgeschakeld" uit de refresh. */
export function Login({ onLoggedIn, message }: { onLoggedIn: (u: User) => void; message?: string | null }) {
  const [setup, setSetup] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  // null = nog niet gevraagd; anders staat 2FA aan en is er een code nodig
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(message ?? null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .setupRequired()
      .then(setSetup)
      .catch((e: Error) => setError(e.message));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(
        setup ? await api.setup(email, name, password) : await api.login(email, password, code ?? undefined),
      );
    } catch (err) {
      if (err instanceof ApiError && err.code === "totp_required") {
        setCode("");
        setError(null);
      } else {
        setError((err as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  if (setup === null && !error) return <main className="center">Laden…</main>;

  return (
    <main className="center">
      <form className="card" onSubmit={submit}>
        <h1>{setup ? "Welkom" : "Inloggen"}</h1>
        {setup && <p className="muted">Maak het eerste account aan; dat wordt de beheerder.</p>}
        <label>
          E-mailadres
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setCode(null); // ander account: misschien zonder 2FA; de server vraagt het zo nodig opnieuw
            }}
          />
        </label>
        {setup && (
          <label>
            Naam
            <input required value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        )}
        <label>
          Wachtwoord
          <input
            type="password"
            autoComplete={setup ? "new-password" : "current-password"}
            required
            minLength={setup ? 10 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {setup && <small className="muted">Minstens 10 tekens.</small>}
        {code !== null && (
          <label>
            Code uit je authenticator-app
            {/* Geen inputMode="numeric": herstelcodes hebben letters, en een cijferklavier kan die niet */}
            <input
              autoComplete="one-time-code"
              autoFocus
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <small className="muted">Telefoon kwijt? Gebruik een van je herstelcodes.</small>
          </label>
        )}
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          {setup ? "Account aanmaken" : "Inloggen"}
        </button>
      </form>
    </main>
  );
}
