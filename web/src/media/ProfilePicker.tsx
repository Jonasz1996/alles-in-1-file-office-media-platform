// Fase 11, stap 7: wie kijkt er? De knop op de start van media toont het profiel van dit toestel;
// het venster kiest een ander (met de pincode als dat profiel er een heeft) en beheert de
// profielen (alleen vanuit een profiel zonder kinderslot). Het token van het gekozen profiel gaat
// mee met elk verzoek naar /api/v1/media (api.ts); de server kijkt het na.

import {
  type FormEvent,
  type JSX,
  useCallback,
  useEffect,
  useState,
} from "react";
import { setMediaProfileToken } from "../api";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { confirm, prompt } from "../ui/dialogs";
import { Field } from "../ui/Field";
import { Icon } from "../ui/Icon";
import { toast } from "../ui/toast";
import {
  createProfile,
  deleteProfile,
  getProfiles,
  selectProfile,
  updateProfile,
} from "./api";
import { setActiveProfile } from "./progressSync";
import type { MediaProfile, MediaProfiles } from "./types";

/** De grenzen van de kijkwijzer; null = geen grens. */
export const AGES = [null, 0, 6, 9, 12, 14, 16] as const;

export function ageLabel(age: number | null): string {
  if (age === null) return t("media.profiles.noLimit");
  return age === 0
    ? t("media.profiles.ageAll")
    : t("media.profiles.age", { n: age });
}

function Editor({
  profile,
  onDone,
}: {
  profile: MediaProfile | null;
  onDone(changed: boolean): void;
}): JSX.Element {
  const self = profile?.id === "self";
  const [name, setName] = useState(profile?.name ?? "");
  const [age, setAge] = useState<string>(
    profile?.max_age == null ? "" : String(profile.max_age),
  );
  const [pin, setPin] = useState("");
  const [clearPin, setClearPin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const badPin = pin !== "" && !/^[0-9]{4,8}$/.test(pin);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (badPin || !name.trim()) return;
    setBusy(true);
    try {
      const maxAge = age === "" ? null : Number(age);
      if (profile === null) {
        await createProfile({
          name: name.trim(),
          max_age: maxAge,
          pin: pin || null,
        });
      } else {
        await updateProfile(profile.id, {
          name: name.trim(),
          ...(self
            ? {}
            : maxAge === null
              ? { clear_age: true }
              : { max_age: maxAge }),
          ...(clearPin ? { clear_pin: true } : pin ? { pin } : {}),
        });
      }
      onDone(true);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <form className="media-profile-editor" onSubmit={(e) => void submit(e)}>
      <h3>
        {profile === null
          ? t("media.profiles.new")
          : t("media.profiles.edit", { name: profile.name })}
      </h3>
      <Field label={t("media.profiles.name")}>
        {(c) => (
          <input
            {...c}
            value={name}
            maxLength={40}
            required
            data-autofocus=""
            onChange={(e) => setName(e.target.value)}
          />
        )}
      </Field>
      {!self && (
        <Field
          label={t("media.profiles.limit")}
          hint={t("media.profiles.limitHint")}
        >
          {(c) => (
            <select {...c} value={age} onChange={(e) => setAge(e.target.value)}>
              {AGES.map((a) => (
                <option key={String(a)} value={a === null ? "" : String(a)}>
                  {ageLabel(a)}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      <Field
        label={
          profile?.has_pin
            ? t("media.profiles.newPin")
            : t("media.profiles.pin")
        }
        hint={
          self ? t("media.profiles.selfPinHint") : t("media.profiles.pinHint")
        }
        error={badPin ? t("media.profiles.pinBad") : null}
      >
        {(c) => (
          <input
            {...c}
            type="password"
            inputMode="numeric"
            autoComplete="off"
            value={pin}
            disabled={clearPin}
            maxLength={8}
            onChange={(e) => setPin(e.target.value)}
          />
        )}
      </Field>
      {profile?.has_pin && (
        <label className="check">
          <input
            type="checkbox"
            checked={clearPin}
            onChange={(e) => setClearPin(e.target.checked)}
          />{" "}
          {t("media.profiles.clearPin")}
        </label>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="media-actions">
        <Button
          type="submit"
          className="primary"
          busy={busy}
          disabled={badPin || !name.trim()}
        >
          {t("media.profiles.save")}
        </Button>
        <Button type="button" onClick={() => onDone(false)}>
          {t("media.profiles.cancel")}
        </Button>
      </div>
    </form>
  );
}

function ProfilesDialog({
  data,
  onClose,
  onChanged,
}: {
  data: MediaProfiles;
  onClose(): void;
  onChanged(switched: boolean): void;
}): JSX.Element {
  const [editing, setEditing] = useState<MediaProfile | null | "new">(null);

  async function pick(p: MediaProfile) {
    if (p.id === data.current) return onClose();
    let pin: string | null = null;
    if (p.has_pin) {
      pin = await prompt({
        title: t("media.profiles.pinTitle", { name: p.name }),
        label: t("media.profiles.pin"),
        type: "password",
        confirm: t("media.profiles.choose"),
      });
      if (pin === null) return;
    }
    try {
      const { token } = await selectProfile(p.id, pin ?? undefined);
      setMediaProfileToken(token);
      setActiveProfile(p.id);
      onChanged(true);
    } catch (err) {
      toast(errorText(err), { kind: "error" });
    }
  }

  async function remove(p: MediaProfile) {
    const ok = await confirm({
      title: t("media.profiles.deleteTitle", { name: p.name }),
      message: t("media.profiles.deleteText"),
      confirm: t("media.profiles.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteProfile(p.id);
      onChanged(false);
    } catch (err) {
      toast(errorText(err), { kind: "error" });
    }
  }

  return (
    <Dialog title={t("media.profiles.title")} onClose={onClose}>
      <ul className="media-profiles">
        {data.profiles.map((p) => (
          <li key={p.id}>
            <button
              type="button"
              className="media-profile"
              aria-pressed={p.id === data.current}
              data-profile={p.id}
              onClick={() => void pick(p)}
            >
              <Icon name="user" size={24} />
              <span className="media-profile-name">{p.name}</span>
              <span className="muted">
                {p.max_age !== null ? ageLabel(p.max_age) : ""}
                {p.has_pin ? ` ${t("media.profiles.locked")}` : ""}
              </span>
            </button>
            {data.can_manage && editing === null && (
              <span className="media-profile-tools">
                <Button onClick={() => setEditing(p)}>
                  {t("media.profiles.editShort")}
                </Button>
                {p.id !== "self" && (
                  <Button onClick={() => void remove(p)}>
                    {t("media.profiles.delete")}
                  </Button>
                )}
              </span>
            )}
          </li>
        ))}
      </ul>
      {data.can_manage && editing === null && (
        <Button onClick={() => setEditing("new")} data-new-profile="">
          {t("media.profiles.new")}
        </Button>
      )}
      {!data.can_manage && (
        <p className="muted">{t("media.profiles.cannotManage")}</p>
      )}
      {editing !== null && (
        <Editor
          profile={editing === "new" ? null : editing}
          onDone={(changed) => {
            setEditing(null);
            if (changed) onChanged(false);
          }}
        />
      )}
    </Dialog>
  );
}

/** De knop "wie kijkt" op de start van media; onSwitch: een ander profiel kijkt nu. */
export function ProfilePicker({
  onSwitch,
}: {
  onSwitch(): void;
}): JSX.Element | null {
  const [data, setData] = useState<MediaProfiles | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await getProfiles();
      setData(next);
      setActiveProfile(next.current);
    } catch {
      setData(null); // zonder profielen (bijv. oude server): geen knop
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!data) return null;
  const current = data.profiles.find((p) => p.id === data.current);
  return (
    <>
      <Button onClick={() => setOpen(true)} data-profiles="">
        <Icon name="user" size={16} />
        {current?.name ?? t("media.profiles.title")}
      </Button>
      {open && (
        <ProfilesDialog
          data={data}
          onClose={() => setOpen(false)}
          onChanged={(switched) => {
            void load();
            if (switched) {
              setOpen(false);
              onSwitch();
            }
          }}
        />
      )}
    </>
  );
}
