import { type FormEvent, useState } from "react";
import { api, type TotpSetup, type User } from "./api";

export function Account({ user, onChange }: { user: User; onChange: () => void }) {
  return (
    <main>
      <h1>Account</h1>
      <p>
        {user.display_name} <span className="muted">({user.email})</span>
      </p>
      <section>
        <h2>Tweestapsverificatie (2FA)</h2>
        {user.totp_enabled ? <TotpOn onChange={onChange} /> : <TotpOff onChange={onChange} />}
      </section>
    </main>
  );
}

function TotpOff({ onChange }: { onChange: () => void }) {
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
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
      setCodes(await api.totpEnable(code));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (codes) return <RecoveryCodes codes={codes} onDone={onChange} />;

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

function TotpOn({ onChange }: { onChange: () => void }) {
  const [mode, setMode] = useState<"idle" | "codes" | "off">("idle");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      if (mode === "codes") setCodes(await api.totpRecoveryCodes(code));
      else {
        await api.totpDisable(password, code);
        onChange();
      }
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (codes) return <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />;

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

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
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
