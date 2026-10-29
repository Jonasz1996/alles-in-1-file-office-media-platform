// De dialoog "Als bibliotheek gebruiken" (fase 5, M2): het soort kiezen (films, series, muziek,
// video's), eventueel een naam, en POST /api/v1/media/libraries. De haak in het menu van een map
// (browser/actions.ts, M0) laadt dit bestand en het gebied "media", en roept
// openNewLibraryDialog aan. De dialoog staat in een eigen React-root buiten de pagina (de actie
// heeft geen plek in de boom van de pagina); ui/Dialog zet de focus erin, Escape sluit, en na het
// sluiten staat de focus weer waar hij was. Een fout van de server (media_library_nested,
// media_library_limit, …) staat in de dialoog, die dan open blijft.

import { type FormEvent, type JSX, useState } from "react";
import { createRoot } from "react-dom/client";
import type { FileNode } from "../api";
import { errorText, t } from "../i18n";
import { navigate } from "../router";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";
import { createLibrary } from "./api";
import { libraryPath, mediaStatus } from "./index";
import { defaultName, guessKind, NAME_MAX, nameToSend } from "./library";
import { LIBRARY_KINDS, type LibraryKind } from "./types";
import "./media.css";

function NewLibrary({ folder, onClose }: { folder: FileNode; onClose(): void }): JSX.Element {
  const [kind, setKind] = useState<LibraryKind>(() => guessKind(folder.name));
  const [name, setName] = useState(() => defaultName(folder.name));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const lib = await createLibrary({ root_id: folder.id, kind, name: nameToSend(name) });
      onClose();
      void mediaStatus.load();
      toast(t("media.newLibrary.created", { name: lib.name }), {
        kind: "ok",
        action: { label: t("media.newLibrary.open"), run: () => navigate(libraryPath(lib.id)) },
      });
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Dialog title={t("media.newLibrary.title")} size="s" onClose={onClose}>
      <form onSubmit={(e) => void submit(e)} className="media-new-library">
        <p className="muted">{t("media.newLibrary.folder", { name: folder.name })}</p>
        <fieldset className="media-kinds">
          <legend>{t("media.newLibrary.kind")}</legend>
          {LIBRARY_KINDS.map((k) => (
            <label key={k} className="media-kind">
              <input
                type="radio"
                name="media-kind"
                value={k}
                checked={kind === k}
                onChange={() => setKind(k)}
                data-autofocus={kind === k ? "" : undefined}
              />
              <span>
                <span className="media-kind-name">{t(`media.kinds.${k}`)}</span>
                <small className="muted">{t(`media.kindHints.${k}`)}</small>
              </span>
            </label>
          ))}
        </fieldset>
        <Field label={t("media.newLibrary.name")} hint={t("media.newLibrary.nameHint")}>
          {(c) => (
            <input {...c} value={name} maxLength={NAME_MAX} autoComplete="off" onChange={(e) => setName(e.target.value)} />
          )}
        </Field>
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" busy={busy}>
            {t("media.newLibrary.create")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Opent de dialoog voor deze map (een map van jezelf, zonder bibliotheek). */
export function openNewLibraryDialog(folder: FileNode): void {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    // Niet midden in een render of een handler van deze root weghalen
    queueMicrotask(() => {
      root.unmount();
      host.remove();
    });
  };
  root.render(<NewLibrary folder={folder} onClose={close} />);
}
