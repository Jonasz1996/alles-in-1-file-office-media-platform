// "Kan niet inloggen?" (op de inlogpagina): e-mailadres in, het commando voor de systeembeheerder
// (app.cli reset-account) uit. Het commando wordt hier in de browser gemaakt, zonder de server te
// vragen: de tekst is dus altijd dezelfde, of er nu een account met dat adres is of niet.

import { type FormEvent, type JSX, useState } from "react";
import { t } from "./i18n";
import { Button } from "./ui/Button";
import { Field } from "./ui/Field";
import { PageTitle } from "./ui/PageTitle";

/** Tussen enkele aanhalingstekens voor de shell; een ' erin wordt '\''. */
function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

/** Het commando voor de systeembeheerder. Het vindt de container van het platform zelf (op de
 * single en in het cluster op de node met het platform), via het label van Docker Compose. */
export function resetCommand(email: string): string {
  return (
    "docker exec -it $(docker ps -qf label=com.docker.compose.service=platform) " +
    `python -m app.cli reset-account ${shellQuote(email.trim().toLowerCase())}`
  );
}

/** "Kan niet inloggen?": e-mailadres in, commando uit. Vraagt niets aan de server. */
export default function CantLogIn({ email: initial, onClose }: { email: string; onClose: () => void }): JSX.Element {
  const [email, setEmail] = useState(initial);
  const [shown, setShown] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  function show(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setShown(email);
    setCopied(false);
  }

  async function copy(command: string) {
    try {
      if (!navigator.clipboard) throw new Error("clipboard");
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setCopied(false); // dan selecteert de gebruiker het zelf
    }
  }

  const command = shown === null ? null : resetCommand(shown);
  return (
    <form className="card login-card" onSubmit={show}>
      <img className="login-logo" src="/icons/icon.svg" alt="" width="48" height="48" />
      <PageTitle>{t("reset.title")}</PageTitle>
      <p className="muted">{t("reset.intro")}</p>
      <Field label={t("auth.email")}>
        {(c) => (
          <input
            {...c}
            type="email"
            autoComplete="username"
            autoFocus
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setShown(null);
            }}
          />
        )}
      </Field>
      {command !== null && (
        <div className="login-reset" aria-live="polite">
          <p>{t("reset.ask")}</p>
          <pre className="login-command">
            <code>{command}</code>
          </pre>
          <p className="muted">{t("reset.after")}</p>
        </div>
      )}
      <div className="login-actions">
        <Button onClick={onClose}>{t("auth.back")}</Button>
        {command === null ? (
          <Button type="submit" variant="primary">
            {t("reset.show")}
          </Button>
        ) : (
          <Button variant="primary" onClick={() => void copy(command)}>
            {copied ? t("reset.copied") : t("reset.copy")}
          </Button>
        )}
      </div>
    </form>
  );
}
