// Kaartachtergrond van Sport (alleen voor een beheerder): het adres van de tegels en de
// bronvermelding (PUT /sport/config). Leeg = geen tegels, niets naar een andere server.

import { type FormEvent, type JSX, useState } from "react";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { type SportConfig, saveSportConfig } from "./api";

export function SettingsDialog({
  config,
  onClose,
  onSaved,
}: {
  config: SportConfig;
  onClose(): void;
  onSaved(c: SportConfig): void;
}): JSX.Element {
  const [url, setUrl] = useState(config.tiles_url ?? "");
  const [attribution, setAttribution] = useState(config.attribution ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e?: FormEvent): Promise<void> {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(await saveSportConfig(url.trim() || null, attribution.trim() || null));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={t("sport.settings.title")}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            {t("sport.settings.save")}
          </Button>
        </>
      }
    >
      <form className="stack" onSubmit={(e) => void save(e)}>
        <p className="muted">{t("sport.settings.intro")}</p>
        <Field label={t("sport.settings.url")} hint={t("sport.settings.urlHint")} error={error}>
          {(c) => (
            <input
              {...c}
              data-autofocus
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("sport.settings.attribution")} hint={t("sport.settings.attributionHint")}>
          {(c) => <input {...c} type="text" value={attribution} onChange={(e) => setAttribution(e.target.value)} />}
        </Field>
      </form>
    </Dialog>
  );
}
