// Inloggen (P1): het eerste account, e-mail en wachtwoord, en de code van de tweede factor.
//
// - Het formulier staat er meteen: of het eerste account nog gemaakt moet worden, wist de
//   refresh van de schil al (api.lastRefresh.setupRequired). Alleen als die het niet zei, vragen
//   we /auth/status.
// - Geen verbinding: een eigen scherm dat het opnieuw probeert zodra de browser online is.
// - Bij 2FA een tweede stap met "Terug"; een blokkade na te veel foute codes toont wanneer het
//   weer kan (params.until van totp_locked).

import { type FormEvent, type JSX, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type User } from "./api";
import { errorText, fmt, isNetworkError, t } from "./i18n";
import { markReady } from "./perf";
import { Button } from "./ui/Button";
import { EmptyState } from "./ui/EmptyState";
import { Field } from "./ui/Field";
import { PageTitle } from "./ui/PageTitle";
import { Spinner } from "./ui/Spinner";
import "./login.css";

const MIN_PASSWORD = 10;

type Step = "login" | "code";

/** Wanneer een blokkade (totp_locked) afloopt, als de server het zei. */
function lockedUntil(e: unknown): number | null {
  if (!(e instanceof ApiError) || e.code !== "totp_locked") return null;
  const until = e.params?.until;
  const at = typeof until === "string" ? Date.parse(until) : Number.NaN;
  if (!Number.isNaN(at)) return at;
  const seconds = Number(e.params?.seconds);
  return Number.isFinite(seconds) ? Date.now() + seconds * 1000 : null;
}

/** De melding bij een blokkade, met de tijd in woorden ("over 4 minuten"). */
function LockMessage({ until }: { until: number }): JSX.Element {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (now >= until) return;
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, [now, until]);
  return (
    <p role="alert" className="login-error">
      {now >= until ? t("auth.lockedOver") : t("auth.locked", { when: fmt.relative(until, now) })}
    </p>
  );
}

/** message: waarom je (weer) moet inloggen, bijv. "Dit account is geblokkeerd" uit de refresh. */
export function Login({ onLoggedIn, message }: { onLoggedIn: (u: User) => void; message?: string | null }) {
  const [setup, setSetup] = useState<boolean | null>(() => api.lastRefresh.setupRequired);
  const [offline, setOffline] = useState(false);
  const [step, setStep] = useState<Step>("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(message ?? null);
  const [lock, setLock] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  const checkStatus = useCallback(() => {
    setOffline(false);
    api
      .setupRequired()
      .then((s) => setSetup(s))
      .catch((e: unknown) => {
        if (isNetworkError(e)) setOffline(true);
        else {
          setSetup(false); // dan toch het gewone formulier, met de fout erbij
          setError(errorText(e));
        }
      });
  }, []);

  useEffect(() => {
    if (setup === null) checkStatus();
  }, [setup, checkStatus]);

  // Weer online: opnieuw proberen
  useEffect(() => {
    if (!offline) return;
    window.addEventListener("online", checkStatus);
    return () => window.removeEventListener("online", checkStatus);
  }, [offline, checkStatus]);

  const ready = setup !== null && !offline;
  useEffect(() => {
    if (ready) markReady("login");
  }, [ready]);

  function fail(err: unknown) {
    const until = lockedUntil(err);
    if (until !== null) {
      setLock(until);
      setError(null);
      return;
    }
    setLock(null);
    setError(errorText(err));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (setup) onLoggedIn(await api.setup(email, name, password));
      else onLoggedIn(await api.login(email, password, step === "code" ? code : undefined));
    } catch (err) {
      if (err instanceof ApiError && err.code === "totp_required" && step === "login") {
        setStep("code");
        setCode("");
        setLock(null);
      } else {
        fail(err);
        if (err instanceof ApiError && err.code === "totp_invalid") setCode("");
      }
    } finally {
      setBusy(false);
    }
  }

  function back() {
    setStep("login");
    setCode("");
    setError(null);
    setLock(null);
    // Terug in het wachtwoordveld (de knop zelf verdwijnt)
    requestAnimationFrame(() => passwordRef.current?.focus());
  }

  if (offline) {
    return (
      <main className="center">
        <EmptyState
          icon="warning"
          title={t("auth.offlineTitle")}
          action={
            <Button variant="primary" onClick={checkStatus}>
              {t("common.retry")}
            </Button>
          }
        >
          <p>{t("auth.offlineText")}</p>
        </EmptyState>
      </main>
    );
  }

  if (setup === null) {
    return (
      <main className="center">
        <Spinner label={t("common.loading")} />
      </main>
    );
  }

  const title = setup ? t("auth.setupTitle") : step === "code" ? t("auth.codeTitle") : t("auth.title");

  return (
    <main className="center login">
      <form className="card login-card" onSubmit={submit}>
        <img className="login-logo" src="/icons/icon.svg" alt="" width="48" height="48" />
        <PageTitle>{title}</PageTitle>
        {setup && <p className="muted">{t("auth.setupIntro")}</p>}
        {step === "login" ? (
          <>
            <Field label={t("auth.email")}>
              {(c) => (
                <input
                  {...c}
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              )}
            </Field>
            {setup && (
              <Field label={t("auth.name")}>
                {(c) => <input {...c} required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />}
              </Field>
            )}
            <Field label={t("auth.password")} hint={setup ? t("auth.passwordHint", { min: MIN_PASSWORD }) : undefined}>
              {(c) => (
                <input
                  {...c}
                  ref={passwordRef}
                  type={showPassword ? "text" : "password"}
                  autoComplete={setup ? "new-password" : "current-password"}
                  required
                  minLength={setup ? MIN_PASSWORD : undefined}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              )}
            </Field>
            <label className="login-toggle">
              <input type="checkbox" checked={showPassword} onChange={(e) => setShowPassword(e.target.checked)} />
              {t("auth.showPassword")}
            </label>
          </>
        ) : (
          <>
            <p className="muted">{t("auth.codeIntro", { email })}</p>
            <Field label={t("auth.code")} hint={t("auth.codeHint")}>
              {(c) => (
                // Geen inputMode="numeric": herstelcodes hebben letters
                <input
                  {...c}
                  autoComplete="one-time-code"
                  autoFocus
                  required
                  spellCheck={false}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              )}
            </Field>
          </>
        )}
        {lock !== null && <LockMessage until={lock} />}
        {error && (
          <p role="alert" className="login-error">
            {error}
          </p>
        )}
        <div className="login-actions">
          {step === "code" && <Button onClick={back}>{t("auth.back")}</Button>}
          <Button type="submit" variant="primary" busy={busy}>
            {setup ? t("auth.createAccount") : t("auth.submit")}
          </Button>
        </div>
        {!setup && step === "login" && <p className="login-forgot muted">{t("auth.forgot")}</p>}
      </form>
    </main>
  );
}
