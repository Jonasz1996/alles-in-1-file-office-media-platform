// /beheer/beveiliging: 2FA verplicht stellen en HTTPS (domein, Let's Encrypt, de status van het
// certificaat en de https-poort). Fouten van het certificaat staan er op code, vertaald.

import { type FormEvent, type JSX, useEffect, useState } from "react";
import { api, type SecurityConfig } from "../api";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { adminApi, type HttpsDetails } from "./api";
import { httpsErrors } from "./format";
import { usePolling } from "./usePolling";

export function SecurityTab(): JSX.Element {
  return (
    <>
      <TwoFactor />
      <Https />
    </>
  );
}

function TwoFactor(): JSX.Element {
  const [config, setConfig] = useState<SecurityConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .security()
      .then((c) => live && setConfig(c))
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, []);

  async function change(require_2fa: boolean) {
    setSaving(true); // één wijziging tegelijk, anders kan een oud antwoord het laatste woord krijgen
    try {
      setConfig(await api.setSecurity({ require_2fa }));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="admin-2fa-title">
      <h2 id="admin-2fa-title">{t("admin.security.title")}</h2>
      {config && (
        <div className="stack">
          <label className="check">
            <input
              type="checkbox"
              checked={config.require_2fa}
              disabled={saving}
              aria-describedby="admin-2fa-help"
              onChange={(e) => void change(e.target.checked)}
            />
            {t("admin.security.require2fa")}
          </label>
          <p id="admin-2fa-help" className="muted">
            {t("admin.security.require2faHelp")}
          </p>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

const BADGE: Record<HttpsDetails["state"], string> = { uit: "degraded", bezig: "degraded", actief: "ok", fout: "down" };

function Https(): JSX.Element {
  const polled = usePolling(adminApi.https, 10_000);
  const [override, setOverride] = useState<HttpsDetails | null>(null);
  const status = override ?? polled.data;
  useEffect(() => setOverride(null), [polled.data]);
  return (
    <section aria-labelledby="admin-https-title">
      <h2 id="admin-https-title">
        {t("admin.https.title")}
        {status && (
          <>
            {": "}
            <span className={`badge ${BADGE[status.state]}`}>{t(`admin.https.state.${status.state}`)}</span>
          </>
        )}
      </h2>
      {polled.error !== null && <p role="alert">{t("admin.refreshFailed", { error: errorText(polled.error) })}</p>}
      {status && <HttpsBody key={status.domain} status={status} onChange={setOverride} />}
    </section>
  );
}

function HttpsBody({ status, onChange }: { status: HttpsDetails; onChange(s: HttpsDetails): void }): JSX.Element {
  const [form, setForm] = useState({ domain: status.domain, email: status.email, staging: status.staging });
  const [editing, setEditing] = useState(!status.domain);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const errors = httpsErrors(status);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      onChange(await api.httpsConfigure(form));
      setEditing(false);
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    setBusy(true);
    try {
      onChange(await api.httpsRenew());
      setError(null);
    } catch (err) {
      setError(errorText(err)); // bijv. te snel na de vorige poging (429)
    } finally {
      setBusy(false);
    }
  }

  const facts: string[] = [];
  if (status.staging) facts.push(t("admin.https.staging"));
  if (status.active && status.expires_at) facts.push(t("admin.https.validUntil", { date: fmt.date(status.expires_at) }));
  if (status.active && status.renew_at) facts.push(t("admin.https.renewFrom", { date: fmt.date(status.renew_at) }));

  return (
    <>
      {status.domain && (
        <p>
          {t("admin.https.domainText", { domain: status.domain })}
          {facts.length > 0 && <span className="muted"> ({facts.join("; ")})</span>}
          {status.active && !status.serving && !status.listener_error && <> {t("admin.https.portSoon")}</>}
        </p>
      )}
      {errors.error && <p role="alert">{errors.error}</p>}
      {errors.listener && <p role="alert">{errors.listener}</p>}
      {status.state === "fout" && status.next_check && (
        <p className="muted">{t("admin.https.nextTry", { time: fmt.dateTime(status.next_check) })}</p>
      )}
      {editing ? (
        <form className="stack" onSubmit={save}>
          <p className="muted">{t("admin.https.help")}</p>
          <Field label={t("admin.https.domain")}>
            {(c) => (
              <input
                {...c}
                placeholder={t("admin.https.domainPlaceholder")}
                value={form.domain}
                onChange={(e) => setForm({ ...form, domain: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("admin.https.email")}>
            {(c) => (
              <input
                {...c}
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            )}
          </Field>
          <label className="check">
            <input
              type="checkbox"
              checked={form.staging}
              onChange={(e) => setForm({ ...form, staging: e.target.checked })}
            />
            {t("admin.https.testMode")}
          </label>
          <div className="toolbar">
            <Button type="submit" variant="primary" busy={busy}>
              {t("common.save")}
            </Button>
            {status.domain && <Button onClick={() => setEditing(false)}>{t("common.cancel")}</Button>}
          </div>
        </form>
      ) : (
        <div className="toolbar">
          <Button onClick={() => setEditing(true)}>{t("admin.https.edit")}</Button>
          {status.state === "fout" && (
            <Button busy={busy} onClick={() => void retry()}>
              {t("admin.https.retry")}
            </Button>
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </>
  );
}
