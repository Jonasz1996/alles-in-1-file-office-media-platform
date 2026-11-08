// De regel van een slim album (fase 6, van Z6; Keuze 42): de knop "Regel" bij een eigen album en
// het dialoog met naam of tekst, personen, soort en periode. Een eigen chunk met het gebied
// photos_people; AlbumPage laadt hem alleen voor de eigenaar.
//
// Na opslaan ververst de server het album meteen. Is het album gedeeld (of heeft het een publieke
// link) en is nog niet gezegd of nieuwe foto's ook gedeeld worden, dan volgt de vraag (shareQuestion).
// Had het album al een regel en is het gedeeld, dan staat die keuze als vinkje in het dialoog. Een
// andere regel zonder dat het vinkje aangeraakt is, stuurt het vinkje niet mee: dan komt de vraag
// opnieuw ("ja" gold voor de regel van toen; review golf 2, veilig V1).
//
// Toetsenbord: Tab door de velden, Enter slaat op (ook vanuit het tekstveld), Esc sluit zonder
// opslaan. In het veld Persoon kies je met ↓, ↑ en Enter.

import { type FormEvent, type JSX, useId, useRef, useState } from "react";
import { errorText, fmt, t } from "../../i18n";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Field } from "../../ui/Field";
import { toast } from "../../ui/toast";
import { type AlbumOut, photosApi } from "../api";
import { deleteAlbumRule, refreshAlbum, setAlbumRule } from "./api";
import { MediaDates } from "./FilterBar";
import { PersonPicker } from "./PersonPicker";
import { formOf, type RuleForm, ruleEmpty, ruleOf, shareAnswer, sharedCount } from "./rule";
import { askShareNew } from "./shareQuestion";
import type { AlbumRuleBody, AlbumRuleInfo } from "./types";
import "./people.css";

/** Een album zoals de eigenaar het krijgt: met de regel (of null). */
export type RuleAlbum = AlbumOut & { rule?: AlbumRuleInfo | null };

export interface AlbumRuleProps {
  album: RuleAlbum;
  /** Na opslaan, weghalen of de vraag: het album opnieuw laden */
  onChanged(): void;
}

export default function AlbumRule({ album, onChanged }: AlbumRuleProps): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button icon="filter" onClick={() => setOpen(true)}>
        {t("photos_people.rule.button")}
      </Button>
      {open && <RuleDialog album={album} onClose={() => setOpen(false)} onChanged={onChanged} />}
    </>
  );
}

function RuleDialog({
  album,
  onClose,
  onChanged,
}: {
  album: RuleAlbum;
  onClose(): void;
  onChanged(): void;
}): JSX.Element {
  const id = useId();
  const info = album.rule ?? null;
  const [form, setForm] = useState<RuleForm>(() => formOf(info));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Heeft de eigenaar het vinkje in dit dialoog zelf aangeraakt? Zo niet en is de regel anders,
  // dan gaat share_new niet mee en komt de vraag opnieuw (review golf 2, veilig V1)
  const [touched, setTouched] = useState(false);
  const qField = useRef<HTMLInputElement>(null);
  // De vraag van Keuze 42 als vinkje: alleen als er al een regel is en het album gedeeld is
  const askNew = info !== null && album.shared;
  const change = (c: Partial<RuleForm>) => setForm((f) => ({ ...f, ...c }));

  async function save(e: FormEvent) {
    e.preventDefault();
    const rule = ruleOf(form, info?.rule);
    if (ruleEmpty(rule)) {
      setError(t("photos_people.rule.needOne"));
      qField.current?.focus();
      return;
    }
    setBusy(true);
    try {
      const answer = askNew ? shareAnswer(form, rule, info?.rule ?? {}, touched) : undefined;
      const body: AlbumRuleBody = answer === undefined ? { rule } : { rule, share_new: answer };
      const saved = await setAlbumRule(album.id, body);
      toast(t("photos_people.rule.saved"), { kind: "ok" });
      onClose();
      if (saved.needs_confirm) {
        const fresh = (await photosApi.album(album.id)) as RuleAlbum;
        await askShareNew(album.id, fresh.rule ?? null);
      }
      onChanged();
    } catch (err) {
      setBusy(false);
      setError(errorText(err));
    }
  }

  async function remove() {
    try {
      await deleteAlbumRule(album.id);
      toast(t("photos_people.rule.removed"), { kind: "ok" });
      onClose();
      onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function refresh() {
    try {
      await refreshAlbum(album.id);
      toast(t("photos_people.rule.refreshing"), { kind: "ok" });
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <Dialog
      title={t("photos_people.rule.title", { name: album.name })}
      onClose={onClose}
      actions={
        <>
          {info && (
            <Button variant="danger" onClick={() => void remove()}>
              {t("photos_people.rule.remove")}
            </Button>
          )}
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" form={`${id}-form`} variant="primary" busy={busy}>
            {t("photos_people.rule.save")}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} className="rule-form" onSubmit={(e) => void save(e)} noValidate>
        <p>{t("photos_people.rule.intro")}</p>
        <Field label={t("photos_people.rule.q")} hint={t("photos_people.rule.qHint")}>
          {(c) => (
            <input
              {...c}
              ref={qField}
              type="text"
              data-autofocus=""
              autoComplete="off"
              value={form.q}
              onChange={(e) => change({ q: e.target.value })}
            />
          )}
        </Field>
        <PersonPicker
          label={t("photos_people.filters.person")}
          hint={t("photos_people.filters.personHint")}
          selected={form.person}
          onChange={(person) => change({ person })}
        />
        <div className="rule-dates">
          <MediaDates id={id} type={form.type} from={form.from} to={form.to} onChange={change} />
        </div>
        {askNew && (
          <div className="field">
            <label className="check">
              <input
                type="checkbox"
                checked={form.shareNew}
                aria-describedby={`${id}-deel`}
                onChange={(e) => {
                  setTouched(true);
                  change({ shareNew: e.target.checked });
                }}
              />
              {t("photos_people.rule.shareNew")}
            </label>
            <small id={`${id}-deel`} className="muted">
              {t("photos_people.rule.shareNewHint")}
            </small>
          </div>
        )}
        {info && (
          <div className="rule-state">
            <p className="muted">
              {t("photos_people.rule.counts", { items: info.items, shared: sharedCount(info) })}{" "}
              {t("photos_people.rule.refreshed", { when: fmt.dateTime(info.refreshed_at) })}
            </p>
            {info.code === "album_rule_too_many" && <p className="field-error">{t("photos_people.rule.tooMany")}</p>}
            <Button variant="link" onClick={() => void refresh()}>
              {t("photos_people.rule.refresh")}
            </Button>
          </div>
        )}
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  );
}
