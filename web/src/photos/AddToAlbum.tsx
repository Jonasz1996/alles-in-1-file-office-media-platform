// "Toevoegen aan album" (P5): eigen foto's en video's aan een eigen album toevoegen, of aan een
// nieuw album. Ook vanuit Bestanden (P2) en de viewer (P4), via AddToAlbumDialog in index.ts.

import { type FormEvent, type JSX, useEffect, useId, useState } from "react";
import { crossApi, type ItemResult } from "../crossApi";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { Spinner } from "../ui/Spinner";
import { toast } from "../ui/toast";
import { type AlbumOut, MAX_ADD, photosApi } from "./api";
import "./photos.css";

export interface AddToAlbumProps {
  nodeIds: string[];
  onClose(): void;
}

const NEW = "new";

/** Voegt toe in stukken van MAX_ADD; geeft het aantal toegevoegd en wat werd overgeslagen. */
export async function addInChunks(
  albumId: string,
  ids: readonly string[],
  add = crossApi.addToAlbum,
): Promise<{ added: number; skipped: ItemResult[] }> {
  let added = 0;
  const skipped: ItemResult[] = [];
  for (let i = 0; i < ids.length; i += MAX_ADD) {
    const r = await add(albumId, ids.slice(i, i + MAX_ADD));
    added += r.added;
    skipped.push(...r.skipped);
  }
  return { added, skipped };
}

export default function AddToAlbumDialog({ nodeIds, onClose }: AddToAlbumProps): JSX.Element {
  const [albums, setAlbums] = useState<AlbumOut[] | null>(null);
  const [choice, setChoice] = useState<string>(NEW);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const group = useId();

  useEffect(() => {
    let live = true;
    photosApi
      .albums()
      .then((r) => {
        if (!live) return;
        const own = r.albums.filter((a) => a.permission === "owner");
        setAlbums(own);
        if (own.length) setChoice(own[0].id);
      })
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, []);

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (busy) return;
    if (choice === NEW && !name.trim()) {
      setError(t("photos.add.nameRequired"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const album =
        choice === NEW ? await photosApi.createAlbum(name.trim()) : (albums ?? []).find((a) => a.id === choice);
      if (!album) return;
      const r = await addInChunks(album.id, nodeIds);
      const already = r.skipped.filter((x) => x.code === "already").length;
      const other = r.skipped.filter((x) => x.code !== "already");
      let message = t("photos.add.done", { n: r.added, album: album.name });
      if (already) message += ` ${t("photos.add.already", { n: already })}`;
      if (other.length) message += ` ${t("photos.add.skipped", { n: other.length, reason: errorText(other[0]) })}`;
      toast(message, { kind: other.length ? "info" : "ok" });
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={t("photos.add.title", { n: nodeIds.length })}
      size="s"
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" busy={busy} disabled={albums === null} onClick={() => void submit()}>
            {t("photos.add.confirm")}
          </Button>
        </>
      }
    >
      <form className="album-choice" onSubmit={submit}>
        {albums === null && !error && <Spinner />}
        {albums && (
          <fieldset>
            <legend className="sr-only">{t("photos.add.which")}</legend>
            {albums.map((a, k) => (
              <label key={a.id} className="album-option">
                <input
                  type="radio"
                  name={group}
                  value={a.id}
                  checked={choice === a.id}
                  onChange={() => setChoice(a.id)}
                  data-autofocus={k === 0 ? "" : undefined}
                />
                <span>{a.name}</span>
                <span className="muted">{t("photos.count", { n: a.count })}</span>
              </label>
            ))}
            <label className="album-option">
              <input
                type="radio"
                name={group}
                value={NEW}
                checked={choice === NEW}
                onChange={() => setChoice(NEW)}
                data-autofocus={albums.length === 0 ? "" : undefined}
              />
              <span>{t("photos.add.newAlbum")}</span>
            </label>
            {choice === NEW && (
              <Field label={t("photos.albumPage.name")}>
                {(c) => (
                  <input
                    {...c}
                    type="text"
                    value={name}
                    maxLength={255}
                    onChange={(e) => setName(e.target.value)}
                  />
                )}
              </Field>
            )}
          </fieldset>
        )}
        {error && <p role="alert">{error}</p>}
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}
