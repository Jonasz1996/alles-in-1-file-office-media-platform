// Een gebruiker toevoegen of wijzigen (naam, e-mailadres, rol, quotum), met het gebruik als balk
// en de acties op het account: blokkeren of deblokkeren, verwijderen (alleen als geblokkeerd),
// een nieuw wachtwoord instellen en 2FA uitzetten. Vragen gaan via confirm() van ui/dialogs.

import { type FormEvent, type JSX, useState } from "react";
import { api, type User } from "../api";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { confirm } from "../ui/dialogs";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";
import { UsageBar } from "../ui/UsageBar";
import { type AdminUser, adminApi, type UserChange } from "./api";
import { parseQuota, quotaInput } from "./format";

const MIN_PASSWORD = 10;
const ROLES: User["role"][] = ["user", "admin", "guest"];

interface Form {
  display_name: string;
  email: string;
  password: string;
  role: User["role"];
  quota: string;
}

export interface UserDialogProps {
  /** null = een nieuwe gebruiker */
  user: AdminUser | null;
  currentUser: User;
  onClose(): void;
  /** Na elke geslaagde wijziging (de lijst opnieuw laden) */
  onChanged(): void;
}

/** Alleen wat anders is dan nu; null als het quotum geen getal is. */
export function changesOf(user: AdminUser, form: Form): UserChange | null {
  const out: UserChange = {};
  const name = form.display_name.trim();
  const email = form.email.trim();
  if (name !== user.display_name) out.display_name = name;
  if (email !== user.email) out.email = email;
  if (form.role !== user.role) out.role = form.role;
  const quota = parseQuota(form.quota);
  if (quota === undefined) return null;
  const bytes = quota ?? 0; // leeg bij wijzigen = onbeperkt
  if (bytes !== user.quota_bytes) out.quota_bytes = bytes;
  return out;
}

export function UserDialog({ user, currentUser, onClose, onChanged }: UserDialogProps): JSX.Element {
  const me = user !== null && user.id === currentUser.id;
  const [form, setForm] = useState<Form>(() => ({
    display_name: user?.display_name ?? "",
    email: user?.email ?? "",
    password: "",
    role: user?.role ?? "user",
    quota: user ? quotaInput(user.quota_bytes) : "",
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [settingPassword, setSettingPassword] = useState(false);
  const formId = "admin-user-form";

  const set = (key: keyof Form) => (e: { target: { value: string } }) => {
    setForm((f) => ({ ...f, [key]: e.target.value }));
    if (key === "quota") setQuotaError(null);
    if (key === "password") setPasswordError(null);
  };

  async function run(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    try {
      await fn();
      setError(null);
      onChanged();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const quota = parseQuota(form.quota);
    if (quota === undefined) {
      setQuotaError(t("admin.user.quotaInvalid"));
      return;
    }
    if (user === null) {
      if (form.password.length < MIN_PASSWORD) {
        setPasswordError(t("admin.user.passwordShort", { min: MIN_PASSWORD }));
        return;
      }
      const name = form.display_name.trim();
      const ok = await run(() =>
        adminApi.createUser({
          email: form.email.trim(),
          display_name: name,
          password: form.password,
          role: form.role,
          quota_bytes: quota, // leeg = null: de standaard van de server
        }),
      );
      if (ok) {
        toast(t("admin.user.created", { name }), { kind: "ok" });
        onClose();
      }
      return;
    }
    const change = changesOf(user, form);
    if (change === null) return;
    if (Object.keys(change).length === 0) {
      onClose();
      return;
    }
    if (await run(() => adminApi.changeUser(user.id, change))) {
      toast(t("admin.user.saved"), { kind: "ok" });
      onClose();
    }
  }

  async function block(disabled: boolean) {
    if (!user) return;
    const name = user.display_name;
    if (disabled) {
      const yes = await confirm({
        title: t("admin.user.blockTitle", { name }),
        message: t("admin.user.blockText", { name }),
        confirm: t("admin.user.block"),
        danger: true,
      });
      if (!yes) return;
    }
    if (await run(() => adminApi.changeUser(user.id, { disabled }))) {
      toast(disabled ? t("admin.user.blocked", { name }) : t("admin.user.unblocked", { name }), { kind: "ok" });
    }
  }

  async function remove() {
    if (!user) return;
    const name = user.display_name;
    const yes = await confirm({
      title: t("admin.user.removeTitle", { name }),
      message: t("admin.user.removeText", { name }),
      confirm: t("admin.user.removeConfirm"),
      danger: true,
      typeToConfirm: user.email,
    });
    if (!yes) return;
    if (await run(() => adminApi.deleteUser(user.id))) {
      toast(t("admin.user.removeStarted", { name }), { kind: "ok" });
      onClose();
    }
  }

  async function reset2fa() {
    if (!user) return;
    const name = user.display_name;
    const yes = await confirm({
      title: t("admin.user.reset2faTitle", { name }),
      message: t("admin.user.reset2faText", { name }),
      confirm: t("admin.user.reset2fa"),
      danger: true,
    });
    if (yes && (await run(() => api.resetTotp(user.id)))) {
      toast(t("admin.user.reset2faDone", { name }), { kind: "ok" });
    }
  }

  const usage = user?.usage;
  const title = user ? t("admin.user.editTitle", { name: user.display_name }) : t("admin.user.newTitle");
  return (
    <Dialog
      title={title}
      size="m"
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" busy={busy}>
            {user ? t("common.save") : t("admin.user.create")}
          </Button>
        </>
      }
    >
      <form id={formId} className="stack admin-user-form" onSubmit={submit} noValidate={false}>
        <Field label={t("admin.user.name")}>
          {(c) => (
            <input {...c} data-autofocus="" required maxLength={100} value={form.display_name} onChange={set("display_name")} />
          )}
        </Field>
        <Field label={t("admin.user.email")}>
          {(c) => <input {...c} type="email" required value={form.email} onChange={set("email")} />}
        </Field>
        {user === null && (
          <Field
            label={t("admin.user.password")}
            hint={t("admin.user.passwordHint", { min: MIN_PASSWORD })}
            error={passwordError}
          >
            {(c) => (
              <input
                {...c}
                type="password"
                autoComplete="new-password"
                required
                minLength={MIN_PASSWORD}
                value={form.password}
                onChange={set("password")}
              />
            )}
          </Field>
        )}
        <Field label={t("admin.user.role")}>
          {(c) => (
            <select {...c} value={form.role} disabled={me} onChange={set("role")}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {t(`admin.roles.${r}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field
          label={t("admin.user.quota")}
          hint={user ? t("admin.user.quotaEditHint") : t("admin.user.quotaHint")}
          error={quotaError}
        >
          {(c) => <input {...c} inputMode="decimal" value={form.quota} onChange={set("quota")} />}
        </Field>
      </form>

      {user && (
        <section className="admin-user-usage" aria-label={t("admin.user.usageLabel", { name: user.display_name })}>
          <UsageBar
            label={t("admin.user.usageLabel", { name: user.display_name })}
            used={usage ? usage.files_bytes + usage.trash_bytes + usage.versions_bytes : user.used_bytes}
            total={user.quota_bytes > 0 ? user.quota_bytes : null}
          />
          {usage && (
            <p className="muted">
              {t("admin.user.usageParts", {
                files: fmt.bytes(usage.files_bytes),
                trash: fmt.bytes(usage.trash_bytes),
                versions: fmt.bytes(usage.versions_bytes),
              })}
              {" · "}
              {t("admin.user.fileCount", { n: usage.file_count })}
            </p>
          )}
        </section>
      )}

      {user && (
        <section className="admin-user-account" aria-labelledby="admin-user-account">
          <h3 id="admin-user-account">{t("admin.user.account")}</h3>
          {me ? (
            <p className="muted">{t("admin.user.own")}</p>
          ) : (
            <div className="toolbar">
              {user.disabled ? (
                <Button disabled={busy || user.deleting} onClick={() => void block(false)}>
                  {t("admin.user.unblock")}
                </Button>
              ) : (
                <Button disabled={busy} onClick={() => void block(true)}>
                  {t("admin.user.block")}
                </Button>
              )}
              <Button disabled={busy || user.disabled === true} onClick={() => setSettingPassword(true)}>
                {t("admin.user.setPassword")}
              </Button>
              {user.totp_enabled && (
                <Button disabled={busy} onClick={() => void reset2fa()}>
                  {t("admin.user.reset2fa")}
                </Button>
              )}
              <Button
                variant="danger"
                disabled={busy || !user.disabled || user.deleting}
                onClick={() => void remove()}
              >
                {t("admin.user.remove")}
              </Button>
            </div>
          )}
          {!me && !user.disabled && <p className="muted">{t("admin.user.removeHint")}</p>}
        </section>
      )}

      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}

      {user && settingPassword && (
        <SetPasswordDialog user={user} onClose={() => setSettingPassword(false)} />
      )}
    </Dialog>
  );
}

/** Een nieuw wachtwoord voor iemand die het zijne kwijt is: twee keer invullen. */
function SetPasswordDialog({ user, onClose }: { user: AdminUser; onClose(): void }): JSX.Element {
  const [first, setFirst] = useState("");
  const [second, setSecond] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = user.display_name;
  const formId = "admin-password-form";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (first.length < MIN_PASSWORD) {
      setProblem(t("admin.user.passwordShort", { min: MIN_PASSWORD }));
      return;
    }
    if (first !== second) {
      setProblem(t("admin.user.passwordMismatch"));
      return;
    }
    setBusy(true);
    try {
      await api.setUserPassword(user.id, first);
      toast(t("admin.user.passwordSet", { name }), { kind: "ok" });
      onClose();
    } catch (err) {
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={t("admin.user.setPasswordTitle", { name })}
      size="s"
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" busy={busy}>
            {t("common.save")}
          </Button>
        </>
      }
    >
      <form id={formId} className="stack" onSubmit={submit}>
        <p className="muted">{t("admin.user.setPasswordText", { name })}</p>
        <Field label={t("admin.user.newPassword")} hint={t("admin.user.passwordHint", { min: MIN_PASSWORD })}>
          {(c) => (
            <input
              {...c}
              data-autofocus=""
              type="password"
              autoComplete="new-password"
              value={first}
              onChange={(e) => {
                setFirst(e.target.value);
                setProblem(null);
              }}
            />
          )}
        </Field>
        <Field label={t("admin.user.repeatPassword")} error={problem}>
          {(c) => (
            <input
              {...c}
              type="password"
              autoComplete="new-password"
              value={second}
              onChange={(e) => {
                setSecond(e.target.value);
                setProblem(null);
              }}
            />
          )}
        </Field>
      </form>
    </Dialog>
  );
}
