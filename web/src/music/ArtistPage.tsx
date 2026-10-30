// /muziek/artiest/:key (fase 5, M9; Keuze 37): een artiest (de sleutel is de genormaliseerde
// albumartiest van de server) met zijn albums, op jaar. ?bib= beperkt tot één bibliotheek;
// zonder: alle muziekbibliotheken die je ziet. Afspelen zet alle albums achter elkaar in de rij.

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { errorText, t } from "../i18n";
import { Link } from "../links";
import { MUSIC_HOME } from "../media";
import { getAlbum, getArtist } from "../media/api";
import { MediaGate } from "../media/Gate";
import type { ArtistDetail } from "../media/types";
import type { RouteProps } from "../router";
import { useQueryParam } from "../router";
import NotFound from "../shellPages";
import { Button } from "../ui/Button";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { toast } from "../ui/toast";
import { AlbumGrid, useMusicOwner } from "./parts";
import { player } from "./player";
import { artistName, toQueue } from "./tracks";

function Artist({ artistKey, params }: { artistKey: string; params: Record<string, string> }): JSX.Element {
  useMusicOwner();
  const [bib] = useQueryParam("bib");
  const [detail, setDetail] = useState<ArtistDetail | null>(null);
  const [error, setError] = useState<{ status?: number; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const gen = useRef(0);
  const load = useCallback(() => {
    const g = ++gen.current;
    getArtist(artistKey, bib ?? undefined).then(
      (d) => {
        if (g !== gen.current) return;
        setDetail(d);
        setError(null);
      },
      (e: unknown) => {
        if (g !== gen.current) return;
        const status = (e as { status?: unknown } | null)?.status;
        setError({ status: typeof status === "number" ? status : undefined, text: errorText(e) });
      },
    );
  }, [artistKey, bib]);
  useEffect(() => {
    setDetail(null);
    load();
  }, [load]);

  /** Alle albums achter elkaar (de nummers per album, op volgorde). */
  const playAll = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const all = await Promise.all(detail.albums.map((a) => getAlbum(a.id)));
      const items = all.flatMap((d) => d.tracks.map((tr) => toQueue(tr, d.album)));
      if (items.length) player.playItems(items, 0);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  if (error?.status === 404) return <NotFound params={params} />;
  if (error) {
    return (
      <main className="music-page">
        <div role="alert" className="music-error">
          <p>{error.text}</p>
          <Button onClick={load}>{t("common.retry")}</Button>
        </div>
      </main>
    );
  }
  if (!detail) {
    return (
      <main className="wide music-page">
        <Skeleton lines={3} />
      </main>
    );
  }
  return (
    <main className="wide music-page">
      <PageTitle
        actions={
          <>
            <Button variant="primary" icon="play" busy={busy} onClick={() => void playAll()} data-play-artist="">
              {t("music.actions.play")}
            </Button>
            <Link to={MUSIC_HOME} className="btn ghost">
              {t("music.title")}
            </Link>
          </>
        }
      >
        {artistName(detail.name)}
      </PageTitle>
      <p className="muted">{t("music.albums.count", { n: detail.albums.length })}</p>
      <AlbumGrid albums={detail.albums} label={t("music.albums.label")} />
    </main>
  );
}

function ArtistRoute({ params }: { params: Record<string, string> }): JSX.Element {
  const key = params.key ?? "";
  return <Artist key={key} artistKey={key} params={params} />;
}

export default function ArtistPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <ArtistRoute params={params} />}</MediaGate>;
}
