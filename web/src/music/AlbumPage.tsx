// /muziek/album/:id (fase 5, M9; Keuze 37): een album met zijn nummers. Afspelen zet het hele
// album in de rij; Enter of een klik op een nummer speelt vanaf dat nummer. Meer (per nummer, en
// voor het album): aan de rij toevoegen en aan een afspeellijst.

import { type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import { artistPath, MUSIC_HOME } from "../media";
import { getAlbum, listArtists } from "../media/api";
import { MediaGate } from "../media/Gate";
import type { AlbumDetail, Artist } from "../media/types";
import type { RouteProps } from "../router";
import NotFound from "../shellPages";
import { Button } from "../ui/Button";
import type { MenuItem } from "../ui/Menu";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import {
  addToMenu,
  Cover,
  enqueueWithToast,
  RowMenu,
  TrackList,
  type TrackRow,
  useMusicOwner,
  usePlayingNode,
  usePlaylists,
} from "./parts";
import { player } from "./player";
import { artistName, multiDisc, toQueue, totalMs, trackLabel } from "./tracks";

/** De sleutel van de albumartiest in de lijst van artiesten (de server normaliseert namen op
 * zijn eigen manier: die regel staat alleen daar). Eerst precies dezelfde naam, dan zonder
 * hoofdletters; null als hij er niet bij staat. */
export function findArtistKey(artists: readonly Artist[], name: string): string | null {
  const exact = artists.find((a) => a.name === name);
  if (exact) return exact.key;
  const lower = name.toLocaleLowerCase();
  return artists.find((a) => a.name.toLocaleLowerCase() === lower)?.key ?? null;
}

/** Het adres van de artiest van dit album (na één vraag om de artiesten), of null. */
function useArtistHref(libraryId: string | null, name: string | null): string | null {
  const [href, setHref] = useState<string | null>(null);
  useEffect(() => {
    setHref(null);
    if (!libraryId || !name) return;
    let live = true;
    listArtists(libraryId).then(
      (all) => {
        const key = live ? findArtistKey(all, name) : null;
        if (key) setHref(`${artistPath(key)}?bib=${encodeURIComponent(libraryId)}`);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, [libraryId, name]);
  return href;
}

function Album({ id, params }: { id: string; params: Record<string, string> }): JSX.Element {
  useMusicOwner();
  const [detail, setDetail] = useState<AlbumDetail | null>(null);
  const [error, setError] = useState<{ status?: number; text: string } | null>(null);
  const gen = useRef(0);
  const load = useCallback(() => {
    const g = ++gen.current;
    getAlbum(id).then(
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
  }, [id]);
  useEffect(() => {
    setDetail(null);
    load();
  }, [load]);
  const { lists, reload } = usePlaylists();
  const playing = usePlayingNode();
  const artistHref = useArtistHref(detail?.album.library_id ?? null, detail?.album.album_artist ?? null);

  const tracks = useMemo(() => detail?.tracks ?? [], [detail]);
  const multi = multiDisc(tracks);
  const rows: TrackRow[] = useMemo(
    () => tracks.map((tr, i) => ({ key: tr.node_id, track: tr, number: trackLabel(tr, multi, i) })),
    [tracks, multi],
  );

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
      <main className="music-page">
        <Skeleton lines={4} />
      </main>
    );
  }

  const album = detail.album;
  const queueItems = () => tracks.map((tr) => toQueue(tr, album));
  const albumMenu = (): MenuItem[] => [
    { id: "queue", label: t("music.actions.queue"), icon: "list", onSelect: () => enqueueWithToast(queueItems()) },
    addToMenu(() => tracks.map((tr) => tr.node_id), lists, reload),
  ];
  const trackMenu = (i: number): MenuItem[] => [
    {
      id: "queue",
      label: t("music.actions.queue"),
      icon: "list",
      onSelect: () => enqueueWithToast([toQueue(tracks[i], album)]),
    },
    addToMenu(() => [tracks[i].node_id], lists, reload),
  ];
  const artist = artistName(album.album_artist);
  const meta = [
    album.year ? String(album.year) : "",
    t("music.tracks.count", { n: tracks.length }),
    totalMs(tracks) > 0 ? fmt.duration(Math.round(totalMs(tracks) / 1000)) : "",
    album.compilation ? t("music.albums.compilation") : "",
  ].filter(Boolean);

  return (
    <main className="music-page">
      <div className="music-head">
        <Cover src={album.cover} size={32} />
        <div className="music-head-text">
          <PageTitle>{album.title}</PageTitle>
          <p className="music-head-artist">{artistHref ? <Link to={artistHref}>{artist}</Link> : artist}</p>
          <p className="muted">{meta.join(" · ")}</p>
          <div className="music-head-actions">
            <Button
              variant="primary"
              icon="play"
              disabled={!tracks.length}
              onClick={() => player.playItems(queueItems(), 0)}
              data-play-album=""
            >
              {t("music.actions.play")}
            </Button>
            <RowMenu label={t("music.actions.more", { title: album.title })} items={albumMenu} tabbable />
            <Link to={MUSIC_HOME} className="btn ghost">
              {t("music.title")}
            </Link>
          </div>
        </div>
      </div>
      <TrackList
        rows={rows}
        label={t("music.tracks.label", { name: album.title })}
        playing={playing}
        onPlay={(i) => player.playItems(queueItems(), i)}
        menu={trackMenu}
      />
    </main>
  );
}

function AlbumRoute({ params }: { params: Record<string, string> }): JSX.Element {
  const id = params.id ?? "";
  return <Album key={id} id={id} params={params} />;
}

export default function AlbumPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <AlbumRoute params={params} />}</MediaGate>;
}
