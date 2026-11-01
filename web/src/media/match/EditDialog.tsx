// Het venster Bewerken (fase 5, M8, Keuze 43): de titel, de sorteertitel, het jaar, de
// samenvatting en (bij een aflevering) het seizoen en het nummer. Elk veld heeft een vinkje
// Vastzetten: wat vastzit, laten de scan en het opzoeken staan. Wat je wijzigt, gaat vanzelf vast;
// het vinkje uit bij een gewijzigd veld zet de wijziging terug. Opslaan stuurt alleen wat anders
// is (PATCH …/items/{id}), plus `unlock` voor wat niet meer vast hoeft.
//
// Bij het openen vraagt het venster het item op met een lege PATCH: die geeft `locked` en de
// sorteertitel, zolang GET …/items/{id} van M2 die nog niet geeft (open punt in m8.md).

import { type FormEvent, type JSX, useEffect, useState } from "react";
import { errorText, t } from "../../i18n";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Skeleton } from "../../ui/Spinner";
import { toast } from "../../ui/toast";
import { updateItem } from "../api";
import type { Item } from "../types";
import { type EditField, type EditForm, editFields, formOf, isEmptyPatch, patchOf, setLock, setValue } from "./match";
import "./match.css";

function FieldRow({
  field,
  form,
  onValue,
  onLock,
}: {
  field: EditField;
  form: EditForm;
  onValue(value: string): void;
  onLock(on: boolean): void;
}): JSX.Element {
  const id = `media-edit-${field}`;
  const label = t(`media.match.fields.${field}`);
  const value = form.values[field];
  const numeric = field === "year" || field === "season" || field === "episode";
  return (
    <div className="field media-edit-row" data-field={field}>
      <label htmlFor={id}>{label}</label>
      {field === "overview" ? (
        <textarea id={id} value={value} rows={5} maxLength={10_000} onChange={(e) => onValue(e.target.value)} />
      ) : (
        <input
          id={id}
          type="text"
          inputMode={numeric ? "numeric" : undefined}
          value={value}
          maxLength={numeric ? 5 : 500}
          autoComplete="off"
          aria-describedby={field === "sort_title" ? `${id}-hint` : undefined}
          data-autofocus={field === "title" ? "" : undefined}
          onChange={(e) => onValue(e.target.value)}
        />
      )}
      {field === "sort_title" && (
        <small id={`${id}-hint`} className="muted">
          {t("media.match.sortHint")}
        </small>
      )}
      <label className="media-edit-lock">
        <input
          type="checkbox"
          checked={form.locked.has(field)}
          data-lock={field}
          onChange={(e) => onLock(e.target.checked)}
        />
        <span>{t("media.match.lock", { field: label })}</span>
      </label>
    </div>
  );
}

export function EditDialog({
  item,
  onClose,
  onDone,
}: {
  item: Item;
  onClose(): void;
  /** Na opslaan, met het nieuwe item */
  onDone(item: Item): void;
}): JSX.Element {
  const [start, setStart] = useState<EditForm | null>(null);
  const [form, setForm] = useState<EditForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    // Een lege PATCH verandert niets en geeft locked en sort_title (zie de kop)
    updateItem(item.node_id, {})
      .then((fresh) => {
        if (!live) return;
        const f = formOf(fresh);
        setStart(f);
        setForm(f);
      })
      .catch((e) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [item.node_id]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!start || !form || busy) return;
    const out = patchOf(item.kind, start, form);
    if (out.problem) {
      setError(t(`media.match.problems.${out.problem}`));
      return;
    }
    if (isEmptyPatch(out.patch)) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await updateItem(item.node_id, out.patch);
      toast(t("media.match.saved"), { kind: "ok" });
      onClose();
      onDone(next);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Dialog title={t("media.match.editTitle")} size="m" onClose={onClose}>
      {!form || !start ? (
        error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : (
          <Skeleton lines={4} />
        )
      ) : (
        <form onSubmit={(e) => void submit(e)} className="media-edit">
          <p className="muted">{t("media.match.lockHint")}</p>
          {editFields(item.kind).map((f) => (
            <FieldRow
              key={f}
              field={f}
              form={form}
              onValue={(v) => setForm((old) => (old ? setValue(old, start, f, v) : old))}
              onLock={(on) => setForm((old) => (old ? setLock(old, start, f, on) : old))}
            />
          ))}
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <Button onClick={onClose}>{t("common.cancel")}</Button>
            <Button type="submit" variant="primary" busy={busy} data-save="">
              {t("media.match.save")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

export default EditDialog;
