// De albums (/albums): je eigen albums en de albums die met je gedeeld zijn, als kaarten met
// de omslag. "Nieuw album" (N) vraagt een naam en opent het nieuwe album.

import { type JSX, useCallback, useEffect, useState } from "react";
import { useReloadOn } from "../events";
import { errorText, t } from "../i18n";
import { Link } from "../links";
import { navigate, type RouteProps, useRestoreScroll } from "../router";
import { Button } from "../ui/Button";
import { prompt } from "../ui/dialogs";
import { EmptyState } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { useScope, useShortcuts } from "../ui/keys";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { toast } from "../ui/toast";
import { type AlbumOut, periodOf, photosApi } from "./api";
import "./photos.css";

/** Vraagt een naam en maakt het album; geeft het nieuwe album of null (geannuleerd). */
export async function newAlbum(): Promise<AlbumOut | null> {
  const name = await prompt({
    title: t("photos.albumPage.new"),
    label: t("photos.albumPage.name"),
    confirm: t("photos.albumPage.create"),
    validate: (v) => (v.trim() ? null : t("photos.add.nameRequired")),
  });
  if (name === null) return null;
  return photosApi.createAlbum(name.trim());
}

export default function AlbumsPage(_: RouteProps): JSX.Element {
  useScope("albums");
  const [albums, setAlbums] = useState<AlbumOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAlbums((await photosApi.albums()).albums);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useReloadOn(
    (e) => e.type === "connected" || e.type.startsWith("album.") || e.type.startsWith("share."),
    () => void load(),
  );
  useRestoreScroll(albums !== null);

  const create = useCallback(async () => {
    try {
      const album = await newAlbum();
      if (album) navigate(`/album/${album.id}`);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }, []);

  useShortcuts([{ combo: "n", label: t("photos.keys.newAlbum"), run: () => void create() }], { scope: "albums" });

  const own = albums?.filter((a) => a.permission === "owner") ?? [];
  const shared = albums?.filter((a) => a.permission !== "owner") ?? [];
  return (
    <main className="wide albums-page">
      <PageTitle
        actions={
          <>
            <Link to="/fotos" className="btn">
              {t("photos.title")}
            </Link>
            <Button variant="primary" icon="album" onClick={() => void create()}>
              {t("photos.albumPage.new")}
            </Button>
          </>
        }
      >
        {t("photos.albums")}
      </PageTitle>
      {error && <p role="alert">{error}</p>}
      {albums === null && !error && <Skeleton />}
      {albums && albums.length === 0 && (
        <EmptyState icon="album" title={t("photos.albumsEmpty.title")}>
          <p>{t("photos.albumsEmpty.text")}</p>
        </EmptyState>
      )}
      {own.length > 0 && <AlbumList title={t("photos.ownAlbums")} albums={own} />}
      {shared.length > 0 && <AlbumList title={t("photos.sharedAlbums")} albums={shared} showOwner />}
    </main>
  );
}

function AlbumList({ title, albums, showOwner = false }: { title: string; albums: AlbumOut[]; showOwner?: boolean }) {
  return (
    <section className="album-section">
      <h2>{title}</h2>
      <ul className="album-cards">
        {albums.map((a) => (
          <li key={a.id}>
            <Link to={`/album/${a.id}`} className="album-card">
              <span className="album-cover" aria-hidden="true">
                {a.cover?.thumb ? <img src={a.cover.thumb} alt="" decoding="async" /> : <Icon name="album" size={32} />}
              </span>
              <span className="album-name">{a.name}</span>
              <span className="muted">
                {t("photos.count", { n: a.count })}
                {periodOf(a) && ` · ${periodOf(a)}`}
              </span>
              {showOwner && <span className="muted">{t("photos.albumPage.by", { name: a.owner.display_name })}</span>}
              {!showOwner && a.shared && <span className="muted">{t("photos.albumPage.isShared")}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
