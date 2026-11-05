// "Onderbroken uploads" in de uploadlade: wat nog op de server ligt van een eerdere keer (na
// herladen, of van een andere computer). Kies het bestand opnieuw (het moet dezelfde naam en
// grootte hebben) en hij gaat verder waar hij was; of gooi hem weg.

import { type JSX, useId, useRef, useState } from "react";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { uploadApi, uploadQueue } from "../uploadQueue";
import { type PendingUpload, tusUrl } from "./api";
import { forgetStoredUrl, interrupted, matches } from "./interrupted";

export function Interrupted({ list }: { list: readonly PendingUpload[] }): JSX.Element {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const choosing = useRef<PendingUpload | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const setError = (entryId: string, text: string | null) =>
    setErrors((all) => {
      const next = { ...all };
      if (text === null) delete next[entryId];
      else next[entryId] = text;
      return next;
    });

  function choose(entry: PendingUpload) {
    choosing.current = entry;
    input.current?.click();
  }

  function picked(el: HTMLInputElement) {
    const entry = choosing.current;
    const file = el.files?.[0];
    el.value = "";
    choosing.current = null;
    if (!entry || !file) return;
    if (!matches(entry, file)) {
      setError(entry.id, t("upload.interrupted.mismatch", { name: entry.name, size: fmt.bytes(entry.size) }));
      return;
    }
    setError(entry.id, null);
    void uploadQueue.resumeUpload(entry, file);
  }

  async function discard(entry: PendingUpload) {
    setBusy(entry.id);
    try {
      await uploadApi.discard(entry.id);
      forgetStoredUrl(tusUrl(entry.id));
      interrupted.remove(entry.id);
    } catch (e) {
      setError(entry.id, t("upload.interrupted.discardFailed", { reason: errorText(e) }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="upload-interrupted" aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>{t("upload.interrupted.title")}</h3>
      <ul className="upload-list">
        {list.map((entry) => (
          <li key={entry.id} className="upload-row">
            <span className="upload-name" title={entry.name}>
              {entry.name}
            </span>
            <span className="upload-status">
              <span className="muted">
                {t("upload.interrupted.progress", {
                  sent: fmt.bytes(entry.offset),
                  size: fmt.bytes(entry.size),
                  percent: fmt.percent(entry.size ? entry.offset / entry.size : 1),
                })}
              </span>
              {errors[entry.id] && (
                <span className="upload-reason" role="alert">
                  {errors[entry.id]}
                </span>
              )}
            </span>
            <span className="upload-actions">
              <Button
                aria-label={t("upload.interrupted.chooseNamed", { name: entry.name })}
                onClick={() => choose(entry)}
              >
                {t("upload.interrupted.choose")}
              </Button>
              <Button
                aria-label={t("upload.interrupted.discardNamed", { name: entry.name })}
                busy={busy === entry.id}
                onClick={() => void discard(entry)}
              >
                {t("upload.interrupted.discard")}
              </Button>
            </span>
          </li>
        ))}
      </ul>
      <div className="upload-tray-actions">
        <Button variant="link" onClick={() => interrupted.clear()}>
          {t("upload.interrupted.later")}
        </Button>
      </div>
      <input ref={input} type="file" hidden onChange={(e) => picked(e.currentTarget)} />
    </section>
  );
}
