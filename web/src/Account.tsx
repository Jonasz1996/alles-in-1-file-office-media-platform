// /account: je opslag, taal en thema, 2FA, je wachtwoord en waar je ingelogd bent. De
// herstelcodes (RecoveryCodes) toont App na 2FA aanzetten of nieuwe codes.

import { type FormEvent, type JSX, useCallback, useEffect, useState } from "react";
import { type Api, api, type TotpSetup, type User } from "./api";
import { useCurrentUser, useShellActions } from "./currentUser";
import { errorText, fmt, t } from "./i18n";
import type { RouteProps } from "./router";
import { Button } from "./ui/Button";
import { confirm } from "./ui/dialogs";
import { Field } from "./ui/Field";
import { LanguageSwitch, ThemeSwitch } from "./ui/switches";
import { toast } from "./ui/toast";
import { UsageBar } from "./ui/UsageBar";

export type AccountSession = Awaited<ReturnType<Api["sessions"]>>[number] & {
  /** Sinds P6: de browser en het besturingssysteem apart (null = onbekend) */
  browser?: string | null;
  os?: string | null;
};

/** Hoe een apparaat heet: "Firefox op Windows" in de taal van de app, anders de tekst van de server. */
export function deviceLabel(s: Pick<AccountSession, "device" | "browser" | "os">): string {
  if (s.browser && s.os) return t("account.sessions.deviceText", { browser: s.browser, os: s.os });
  return s.browser || s.os || s.device || t("account.sessions.unknownDevice");
}

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
}): JSX.Element {
  // Na een nieuw wachtwoord (of 2FA aan of uit) zijn de andere sessies uitgelogd: de lijst
  // opnieuw ophalen
  const [passwordChanged, setPasswordChanged] = useState(0);

  return (
    <main className="account">
      <h1>{t("account.title")}</h1>
      <p>
        {user.display_name} <span className="muted">({user.email})</span>
      </p>
      {user.totp_setup_required && (
        <p>
          <strong>{t("account.required2fa")}</strong> {t("account.required2faText")}
        </p>
      )}
      {!user.totp_setup_required && (
        <section aria-labelledby="account-storage">
          <h2 id="account-storage">{t("account.storage.title")}</h2>
          <UsageBar
            label={t("account.storage.label")}
            used={user.used_bytes}
            total={user.quota_bytes > 0 ? user.quota_bytes : null}
          />
          {user.quota_bytes <= 0 && <p className="muted">{t("account.storage.unlimited")}</p>}
        </section>
      )}
      <section aria-labelledby="account-look">
        <h2 id="account-look">{t("account.look.title")}</h2>
        <div className="stack">
          <LanguageSwitch />
          <ThemeSwitch />
          <p className="muted">{t("account.look.themeHelp")}</p>
        </div>
      </section>
      <section aria-labelledby="account-2fa">
        <h2 id="account-2fa">{t("account.totp.title")}</h2>
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
      setError(t("account.password.mismatch"));
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
      setError(errorText(err)); // ook "Te veel foute …" (429)
    } finally {
      setBusy(false);
    }
  }

  const field = (key: keyof typeof empty) => ({
    value: form[key],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value }),
  });

  return (
    <section aria-labelledby="account-password">
      <h2 id="account-password">{t("account.password.title")}</h2>
      <form className="stack" onSubmit={submit}>
        <Field label={t("account.password.current")}>
          {(c) => <input {...c} type="password" autoComplete="current-password" required {...field("current")} />}
        </Field>
        <Field label={t("account.password.next")} hint={t("account.password.hint", { min: MIN_PASSWORD })}>
          {(c) => (
            <input
              {...c}
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD}
              {...field("next")}
            />
          )}
        </Field>
        <Field label={t("account.password.repeat")}>
          {(c) => (
            <input
              {...c}
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD}
              {...field("repeat")}
            />
          )}
        </Field>
        {totpEnabled && (
          <Field label={t("account.password.code")}>
            {(c) => <input {...c} autoComplete="one-time-code" required {...field("code")} />}
          </Field>
        )}
        <div className="toolbar">
          <Button type="submit" busy={busy}>
            {t("account.password.submit")}
          </Button>
        </div>
        {done && (
          <p>
            <span className="badge ok">{t("account.password.done")}</span> {t("account.password.doneText")}
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
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, reload]);

  async function end(s: AccountSession) {
    if (s.current) {
      const yes = await confirm({
        title: t("account.sessions.currentTitle"),
        message: t("account.sessions.currentText"),
        confirm: t("account.sessions.signOut"),
      });
      if (!yes) return;
    }
    try {
      await api.revokeSession(s.id, s.current); // de eigen sessie: de app logt uit
      if (!s.current) {
        toast(t("account.sessions.signedOut", { device: deviceLabel(s) }), { kind: "ok" });
        await load();
      }
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function endOthers() {
    const yes = await confirm({
      title: t("account.sessions.othersTitle"),
      message: t("account.sessions.othersText"),
      confirm: t("account.sessions.othersConfirm"),
      danger: true,
    });
    if (!yes) return;
    try {
      await api.revokeOtherSessions();
      toast(t("account.sessions.othersDone"), { kind: "ok" });
      await load();
    } catch (e) {
      setError(errorText(e));
    }
  }

  const others = sessions?.filter((s) => !s.current).length ?? 0;

  return (
    <section aria-labelledby="account-sessions">
      <h2 id="account-sessions">{t("account.sessions.title")}</h2>
      <p className="muted">{t("account.sessions.help")}</p>
      {error && <p role="alert">{error}</p>}
      {sessions && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("account.sessions.device")}</th>
                <th className="wide-only">{t("account.sessions.ip")}</th>
                <th>{t("account.sessions.lastActive")}</th>
                <th className="wide-only">{t("account.sessions.since")}</th>
                <th>
                  <span className="sr-only">{t("account.sessions.signOut")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>
                    {deviceLabel(s)}
                    {s.current && (
                      <>
                        {" "}
                        <span className="badge ok">{t("account.sessions.current")}</span>
                      </>
                    )}
                  </td>
                  <td className="wide-only">{s.ip ?? "–"}</td>
                  <td>{fmt.dateTime(s.last_active_at)}</td>
                  <td className="wide-only">{fmt.dateTime(s.created_at)}</td>
                  <td className="actions">
                    <Button
                      aria-label={t("account.sessions.signOutLabel", { device: deviceLabel(s) })}
                      onClick={() => void end(s)}
                    >
                      {t("account.sessions.signOut")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="toolbar">
        <Button onClick={() => void endOthers()} disabled={others === 0}>
          {t("account.sessions.signOutOthers")}
        </Button>
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
      setError(errorText(e));
    }
  }

  async function enable(e: FormEvent) {
    e.preventDefault();
    try {
      const codes = await api.totpEnable(code, password);
      setError(null);
      onEnabled(codes);
    } catch (err) {
      setError(errorText(err)); // ook "Te veel foute codes…" (429)
    }
  }

  if (!setup) {
    return (
      <>
        <p>{t("account.totp.intro")}</p>
        <Button onClick={() => void start()}>{t("account.totp.turnOn")}</Button>
        {error && <p role="alert">{error}</p>}
      </>
    );
  }

  return (
    <form className="stack" onSubmit={enable}>
      <p>{t("account.totp.scan")}</p>
      <img className="qr" alt={t("account.totp.qrAlt")} src={setup.qr} />
      <p className="muted">
        {t("account.totp.manual")} <code>{groups(setup.secret)}</code>
      </p>
      <Field label={t("account.totp.code")}>
        {(c) => (
          <input
            {...c}
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        )}
      </Field>
      <Field label={t("account.totp.password")}>
        {(c) => (
          <input
            {...c}
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        )}
      </Field>
      <div className="toolbar">
        <Button type="submit" variant="primary">
          {t("account.totp.enable")}
        </Button>
        <Button onClick={() => setSetup(null)}>{t("common.cancel")}</Button>
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
      setError(errorText(err));
    }
  }

  return (
    <>
      <p>
        <span className="badge ok">{t("account.totp.on")}</span> {t("account.totp.onText")}
      </p>
      {mode === "idle" ? (
        <div className="toolbar">
          <Button onClick={() => setMode("codes")}>{t("account.totp.newCodes")}</Button>
          <Button onClick={() => setMode("off")}>{t("account.totp.turnOff")}</Button>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          {mode === "off" && (
            <Field label={t("account.totp.passwordLabel")}>
              {(c) => (
                <input
                  {...c}
                  data-autofocus=""
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              )}
            </Field>
          )}
          <Field label={t("account.totp.codeLabel")}>
            {(c) => (
              <input {...c} autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} />
            )}
          </Field>
          <div className="toolbar">
            <Button type="submit" variant={mode === "off" ? "danger" : "primary"}>
              {mode === "off" ? t("account.totp.turnOff") : t("account.totp.makeCodes")}
            </Button>
            <Button onClick={() => setMode("idle")}>{t("common.cancel")}</Button>
          </div>
          {error && <p role="alert">{error}</p>}
        </form>
      )}
    </>
  );
}

/** De route /account: de ingelogde gebruiker en de acties van de schil. */
export default function AccountPage(_: RouteProps): JSX.Element | null {
  const user = useCurrentUser();
  const shell = useShellActions();
  if (!user) return null;
  return <Account user={user} onChange={shell.reloadUser} onCodes={shell.showRecoveryCodes} />;
}

export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }): JSX.Element {
  function download() {
    const text = `${t("account.codes.fileText")}\n\n${codes.join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = t("account.codes.fileName");
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  return (
    <div className="stack">
      <p>
        <strong>{t("account.codes.keep")}</strong> {t("account.codes.keepText")}
      </p>
      <ul className="codes">
        {codes.map((c) => (
          <li key={c}>
            <code>{c}</code>
          </li>
        ))}
      </ul>
      <div className="toolbar">
        <Button onClick={download}>{t("account.codes.download")}</Button>
        <Button variant="primary" onClick={onDone}>
          {t("account.codes.done")}
        </Button>
      </div>
    </div>
  );
}

function groups(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}
