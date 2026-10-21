import { type FormEvent, useCallback, useEffect, useState } from "react";
import { type Api, api, formatDate, type TotpSetup, type User } from "./api";
import { useCurrentUser, useShellActions } from "./currentUser";
import type { RouteProps } from "./router";

export type AccountSession = Awaited<ReturnType<Api["sessions"]>>[number];

/**
 * onChange: 2FA ging aan of uit, de gebruiker moet opnieuw opgehaald worden.
 * onCodes: nieuwe herstelcodes; App toont ze (en ververst de gebruiker).
 */
export function Account({
  user,
  onChange,
  onCodes,
}: {
  user: User;
  onChange: () => void;
  onCodes: (codes: string[]) => void;
}) {
  // Na een nieuw wachtwoord (of 2FA aan of uit) zijn de andere sessies uitgelogd: de lijst
  // opnieuw ophalen
  const [passwordChanged, setPasswordChanged] = useState(0);

  return (
    <main>
      <h1>Account</h1>
      <p>
        {user.display_name} <span className="muted">({user.email})</span>
      </p>
      {user.totp_setup_required && (
        <p>
          <strong>Op dit platform is 2FA verplicht.</strong> Zet het hieronder aan; daarna kun je bij je bestanden.
        </p>
      )}
      <section>
        <h2>Tweestapsverificatie (2FA)</h2>
        {user.totp_enabled ? <TotpOn onChange={onChange} onCodes={onCodes} /> : <TotpOff onEnabled={onCodes} />}
      </section>
      {!user.totp_setup_required && (
        <>
          <PasswordChange totpEnabled={user.totp_enabled} onChanged={() => setPasswordChanged((n) => n + 1)} />
          <Sessions reload={`${passwordChanged}-${user.totp_enabled}`} />
        </>
      )}
    </main>
  );
}

const MIN_PASSWORD = 10;

function PasswordChange({ totpEnabled, onChanged }: { totpEnabled: boolean; onChanged: () => void }) {
  const empty = { current: "", next: "", repeat: "", code: "" };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setDone(false);
    if (form.next !== form.repeat) {
      setError("De twee nieuwe wachtwoorden zijn niet gelijk.");
      return;
    }
    setBusy(true);
    try {
      await api.changePassword(form.current, form.next, totpEnabled ? form.code : undefined);
      setForm(empty);
      setError(null);
      setDone(true);
      onChanged();
    } catch (err) {
      setError((err as Error).message); // ook "Te veel foute …" (429)
    } finally {
      setBusy(false);
    }
  }

  const field = (key: keyof typeof empty) => ({
    value: form[key],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value }),
  });

  return (
    <section>
      <h2>Wachtwoord wijzigen</h2>
      <form className="stack" onSubmit={submit}>
        <label>
          Huidig wachtwoord
          <input type="password" autoComplete="current-password" required {...field("current")} />
        </label>
        <label>
          Nieuw wachtwoord
          <input type="password" autoComplete="new-password" required minLength={MIN_PASSWORD} {...field("next")} />
          <small className="muted">Minstens {MIN_PASSWORD} tekens.</small>
        </label>
        <label>
          Nieuw wachtwoord nog een keer
          <input type="password" autoComplete="new-password" required minLength={MIN_PASSWORD} {...field("repeat")} />
        </label>
        {totpEnabled && (
          <label>
            Code uit je app (of een herstelcode)
            <input autoComplete="one-time-code" required {...field("code")} />
          </label>
        )}
        <div className="toolbar">
          <button type="submit" disabled={busy}>
            Wachtwoord wijzigen
          </button>
        </div>
        {done && (
          <p>
            <span className="badge ok">Je wachtwoord is gewijzigd.</span> Op je andere apparaten ben je uitgelogd.
          </p>
        )}
        {error && <p role="alert">{error}</p>}
      </form>
    </section>
  );
}

function Sessions({ reload }: { reload: string }) {
  const [sessions, setSessions] = useState<AccountSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSessions(await api.sessions());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, reload]);

  async function end(s: AccountSession) {
    if (s.current && !window.confirm("Dit is het apparaat dat je nu gebruikt. Hier uitloggen?")) return;
    try {
      await api.revokeSession(s.id, s.current); // de eigen sessie: de app logt uit
      if (!s.current) await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function endOthers() {
    if (!window.confirm("Op alle andere apparaten uitloggen?")) return;
    try {
      await api.revokeOtherSessions();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const others = sessions?.filter((s) => !s.current).length ?? 0;

  return (
    <section>
      <h2>Apparaten en sessies</h2>
      <p className="muted">
        Hier ben je ingelogd. Herken je een apparaat niet, log het dan uit en wijzig je wachtwoord.
      </p>
      {error && <p role="alert">{error}</p>}
      {sessions && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Apparaat</th>
                <th>IP-adres</th>
                <th>Laatst actief</th>
                <th>Ingelogd sinds</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.device}
                    {s.current && (
                      <>
                        {" "}
                        <span className="badge ok">Dit apparaat</span>
                      </>
                    )}
                  </td>
                  <td>{s.ip ?? "-"}</td>
                  <td>{formatDate(s.last_active_at)}</td>
                  <td>{formatDate(s.created_at)}</td>
                  <td className="actions">
                    <button type="button" onClick={() => end(s)}>
                      Uitloggen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="toolbar">
        <button type="button" onClick={endOthers} disabled={others === 0}>
          Overal anders uitloggen
        </button>
      </div>
    </section>
  );
}

function TotpOff({ onEnabled }: { onEnabled: (codes: string[]) => void }) {
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function start() {
    try {
      setSetup(await api.totpSetup());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function enable(e: FormEvent) {
    e.preventDefault();
    try {
      const codes = await api.totpEnable(code, password);
      setError(null);
      onEnabled(codes);
    } catch (err) {
      setError((err as Error).message); // ook "Te veel foute codes…" (429)
    }
  }

  if (!setup) {
    return (
      <>
        <p>
          Met 2FA heb je bij het inloggen naast je wachtwoord ook een code uit een app op je telefoon nodig, zoals
          Aegis, Google Authenticator of 1Password. Zet dit aan voordat het platform van buiten bereikbaar is.
        </p>
        <button type="button" onClick={start}>
          2FA aanzetten
        </button>
        {error && <p role="alert">{error}</p>}
      </>
    );
  }

  return (
    <form className="stack" onSubmit={enable}>
      <p>1. Scan deze QR-code met je authenticator-app.</p>
      <img className="qr" alt="QR-code voor je authenticator-app" src={setup.qr} />
      <p className="muted">
        Scannen lukt niet? Vul deze sleutel met de hand in: <code>{groups(setup.secret)}</code>
      </p>
      <label>
        2. Vul de code in die de app nu toont
        <input
          inputMode="numeric"
          autoComplete="one-time-code"
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      <label>
        3. Je wachtwoord, ter bevestiging
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <div className="toolbar">
        <button type="submit">Aanzetten</button>
        <button type="button" onClick={() => setSetup(null)}>
          Annuleren
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}

function TotpOn({ onChange, onCodes }: { onChange: () => void; onCodes: (codes: string[]) => void }) {
  const [mode, setMode] = useState<"idle" | "codes" | "off">("idle");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      if (mode === "codes") {
        const codes = await api.totpRecoveryCodes(code);
        setError(null);
        onCodes(codes);
      } else {
        await api.totpDisable(password, code);
        setError(null);
        onChange();
      }
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <>
      <p>
        <span className="badge ok">2FA staat aan.</span> Bij het inloggen vraagt het platform om een code uit je app.
      </p>
      {mode === "idle" ? (
        <div className="toolbar">
          <button type="button" onClick={() => setMode("codes")}>
            Nieuwe herstelcodes
          </button>
          <button type="button" onClick={() => setMode("off")}>
            2FA uitzetten
          </button>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          {mode === "off" && (
            <label>
              Wachtwoord
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          )}
          <label>
            Code uit je app (of een herstelcode)
            <input autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <div className="toolbar">
            <button type="submit">{mode === "off" ? "2FA uitzetten" : "Maak nieuwe codes"}</button>
            <button type="button" onClick={() => setMode("idle")}>
              Annuleren
            </button>
          </div>
          {error && <p role="alert">{error}</p>}
        </form>
      )}
    </>
  );
}

/** De route /account: de ingelogde gebruiker en de acties van de schil. */
export default function AccountPage(_: RouteProps) {
  const user = useCurrentUser();
  const shell = useShellActions();
  if (!user) return null;
  return <Account user={user} onChange={shell.reloadUser} onCodes={shell.showRecoveryCodes} />;
}

export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  function download() {
    const text = `Herstelcodes voor het platform. Elke code werkt één keer.\n\n${codes.join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "herstelcodes.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  return (
    <div className="stack">
      <p>
        <strong>Bewaar deze herstelcodes op een veilige plek.</strong> Ben je je telefoon kwijt, dan log je met een
        van deze codes in. Elke code werkt één keer, en je ziet ze maar één keer.
      </p>
      <ul className="codes">
        {codes.map((c) => (
          <li key={c}>
            <code>{c}</code>
          </li>
        ))}
      </ul>
      <div className="toolbar">
        <button type="button" onClick={download}>
          Downloaden als tekstbestand
        </button>
        <button type="button" onClick={onDone}>
          Ik heb ze bewaard
        </button>
      </div>
    </div>
  );
}

function groups(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}
