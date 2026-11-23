// "Wachtwoord vergeten of kan niet inloggen?" (op de inlogpagina): login in, een code uit. De server
// geeft voor elke login een willekeurige code, gekoppeld aan een reset-verzoek dat 24 uur geldt
// (POST /auth/reset-request). Het antwoord is altijd hetzelfde, of het account nu bestaat of niet:
// pas de systeembeheerder ziet het, met `app.cli reset-account <code>`.

import { type FormEvent, type JSX, useState } from "react";
import { errorFromBody, fetchOrNetworkError } from "./api";
import { errorText, fmt, t } from "./i18n";
import { Button } from "./ui/Button";
import { Field } from "./ui/Field";
import { PageTitle } from "./ui/PageTitle";

/** Het commando voor de systeembeheerder, met de code erin. Het vindt de container van het
 * platform zelf (op de single en in het cluster op de node met het platform), via het label van
 * Docker Compose. De code bestaat alleen uit letters, cijfers en streepjes: geen aanhalingstekens
 * nodig. */
export function resetCommand(code: string): string {
  return (
    "docker exec -it $(docker ps -qf label=com.docker.compose.service=platform) " +
    `python -m app.cli reset-account ${code.replace(/[^A-Za-z0-9-]/g, "")}`
  );
}

type Issued = { code: string; expires_at: string };

async function askCode(login: string): Promise<Issued> {
  const res = await fetchOrNetworkError(fetch, "/api/v1/auth/reset-request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login }),
  });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw errorFromBody(res.status, body);
  return body as Issued;
}

/** Login in, code uit. */
export default function CantLogIn({ email: initial, onClose }: { email: string; onClose: () => void }): JSX.Element {
  const [login, setLogin] = useState(initial);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"code" | "command" | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!login.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setIssued(await askCode(login.trim()));
      setCopied(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy(what: "code" | "command", text: string) {
    try {
      if (!navigator.clipboard) throw new Error("clipboard");
      await navigator.clipboard.writeText(text);
      setCopied(what);
    } catch {
      setCopied(null); // dan selecteert de gebruiker het zelf
    }
  }

  if (issued !== null) {
    const command = resetCommand(issued.code);
    return (
      <div className="card login-card">
        <img className="login-logo" src="/icons/icon.svg" alt="" width="48" height="48" />
        <PageTitle>{t("reset.sentTitle")}</PageTitle>
        <div className="login-reset" aria-live="polite">
          <p>{t("reset.done")}</p>
          <p className="login-code">
            <code>{issued.code}</code>
          </p>
          <p className="muted">{t("reset.valid", { until: fmt.dateTime(issued.expires_at) })}</p>
          <p>{t("reset.ask")}</p>
          <pre className="login-command">
            <code>{command}</code>
          </pre>
          <p className="muted">{t("reset.after")}</p>
        </div>
        <div className="login-actions">
          <Button onClick={onClose}>{t("auth.back")}</Button>
          <Button onClick={() => void copy("command", command)}>
            {copied === "command" ? t("reset.copied") : t("reset.copyCommand")}
          </Button>
          <Button variant="primary" onClick={() => void copy("code", issued.code)}>
            {copied === "code" ? t("reset.copied") : t("reset.copy")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form className="card login-card" onSubmit={(e) => void submit(e)}>
      <img className="login-logo" src="/icons/icon.svg" alt="" width="48" height="48" />
      <PageTitle>{t("reset.title")}</PageTitle>
      <p className="muted">{t("reset.intro")}</p>
      <Field label={t("reset.login")}>
        {(c) => (
          <input
            {...c}
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            required
            maxLength={320}
            value={login}
            onChange={(e) => setLogin(e.target.value)}
          />
        )}
      </Field>
      {error && (
        <p role="alert" className="login-error">
          {error}
        </p>
      )}
      <div className="login-actions">
        <Button onClick={onClose}>{t("auth.back")}</Button>
        <Button type="submit" variant="primary" disabled={busy}>
          {t("reset.show")}
        </Button>
      </div>
    </form>
  );
}
