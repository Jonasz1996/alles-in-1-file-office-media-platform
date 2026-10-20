import { type FormEvent, useState } from "react";
import { api, type TotpSetup, type User } from "./api";

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
    </main>
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
