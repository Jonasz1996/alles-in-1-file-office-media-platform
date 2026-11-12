// /muziek/lijst/:id (fase 5, M9; Keuze 37): een afspeellijst van jezelf. Afspelen, per nummer:
// spelen, aan de rij, omhoog, omlaag en uit de lijst. Met het toetsenbord ook op de regel zelf:
// Alt+pijl omhoog/omlaag verplaatst het nummer, Delete haalt het uit de lijst.
//
// Een nummer dat je niet meer mag lezen, laat de server weg (niet weggegooid). De volgorde gaat
// als de hele lijst van zichtbare regels naar de server (PUT …/order).

import { type JSX, type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import { MUSIC_HOME } from "../media";
import { getPlaylist, removeFromPlaylist, reorderPlaylist } from "../media/api";
import { MediaGate } from "../media/Gate";
import type { PlaylistDetail } from "../media/types";
import type { RouteProps } from "../router";
import { navigate } from "../router";
import NotFound from "../shellPages";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import type { MenuItem } from "../ui/Menu";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { announce, toast } from "../ui/toast";
import {
  enqueueWithToast,
  playlistActions,
  RowMenu,
  TrackList,
  type TrackRow,
  useMusicOwner,
  usePlayingNode,
} from "./parts";
import { player } from "./player";
import { toQueue, totalMs } from "./tracks";

/** De lijst met regel from op plek to (puur; voor de volgorde naar de server). */
export function moved<T>(items: readonly T[], from: number, to: number): T[] {
  if (from < 0 || from >= items.length || to < 0 || to >= items.length || from === to) return [...items];
  const out = [...items];
  const [x] = out.splice(from, 1);
  out.splice(to, 0, x);
  return out;
}

function List({ id, params }: { id: string; params: Record<string, string> }): JSX.Element {
  useMusicOwner();
  const [detail, setDetail] = useState<PlaylistDetail | null>(null);
  const [error, setError] = useState<{ status?: number; text: string } | null>(null);
  const gen = useRef(0);
  const load = useCallback(() => {
    const g = ++gen.current;
    getPlaylist(id).then(
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
  const playing = usePlayingNode();
  const items = useMemo(() => detail?.items ?? [], [detail]);
  const rows: TrackRow[] = useMemo(
    () => items.map((it, i) => ({ key: it.id, track: it.track, number: String(i + 1) })),
    [items],
  );

  const move = useCallback(
    async (from: number, to: number, focus?: (i: number) => void) => {
      if (!detail || to < 0 || to >= items.length || from === to) return;
      const next = moved(items, from, to);
      const title = items[from].track.title;
      setDetail({ ...detail, items: next });
      focus?.(to);
      try {
        await reorderPlaylist(detail.playlist.id, next.map((x) => x.id));
        announce(t("music.playlists.moved", { title, n: to + 1 }));
      } catch (e) {
        toast(errorText(e), { kind: "error" });
        load();
      }
    },
    [detail, items, load],
  );

  const remove = useCallback(
    async (i: number, focus?: (i: number) => void) => {
      if (!detail) return;
      const item = items[i];
      try {
        await removeFromPlaylist(detail.playlist.id, item.id);
        const next = items.filter((x) => x.id !== item.id);
        setDetail({
          ...detail,
          playlist: {
            ...detail.playlist,
            item_count: Math.max(0, detail.playlist.item_count - 1),
            duration_ms: Math.max(0, detail.playlist.duration_ms - (item.track.duration_ms ?? 0)),
          },
          items: next,
        });
        toast(t("music.playlists.removed", { title: item.track.title }), { kind: "ok" });
        if (next.length) focus?.(Math.min(i, next.length - 1));
      } catch (e) {
        toast(errorText(e), { kind: "error" });
      }
    },
    [detail, items],
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

  const list = detail.playlist;
  const queueItems = () => items.map((it) => toQueue(it.track));
  const rowMenu = (i: number): MenuItem[] => [
    { id: "queue", label: t("music.actions.queue"), icon: "list", onSelect: () => enqueueWithToast([toQueue(items[i].track)]) },
    {
      id: "up",
      label: t("music.playlists.up"),
      icon: "chevron-up",
      shortcut: "alt+up",
      disabled: i === 0,
      onSelect: () => void move(i, i - 1),
    },
    {
      id: "down",
      label: t("music.playlists.down"),
      icon: "chevron-down",
      shortcut: "alt+down",
      disabled: i === items.length - 1,
      onSelect: () => void move(i, i + 1),
    },
    {
      id: "remove",
      label: t("music.playlists.remove"),
      icon: "trash",
      shortcut: "delete",
      danger: true,
      onSelect: () => void remove(i),
    },
  ];
  const onKey = (e: KeyboardEvent<HTMLElement>, i: number, focus: (i: number) => void): boolean => {
    if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      void move(i, e.key === "ArrowUp" ? i - 1 : i + 1, focus);
      return true;
    }
    if (e.key === "Delete" && !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      void remove(i, focus);
      return true;
    }
    return false;
  };
  const meta = [t("music.tracks.count", { n: items.length })];
  if (totalMs(items.map((x) => x.track)) > 0) meta.push(fmt.duration(Math.round(totalMs(items.map((x) => x.track)) / 1000)));

  return (
    <main className="music-page">
      <PageTitle
        actions={
          <>
            <Button
              variant="primary"
              icon="play"
              disabled={!items.length}
              onClick={() => player.playItems(queueItems(), 0)}
              data-play-list=""
            >
              {t("music.actions.play")}
            </Button>
            <RowMenu
              label={t("music.playlists.more", { name: list.name })}
              items={() => playlistActions(list, load, () => navigate(`${MUSIC_HOME}?tab=lijsten`))}
              tabbable
            />
            <Link to={`${MUSIC_HOME}?tab=lijsten`} className="btn ghost">
              {t("music.playlists.label")}
            </Link>
          </>
        }
      >
        {list.name}
      </PageTitle>
      <p className="muted">{meta.join(" · ")}</p>
      {list.item_count > items.length && <p className="muted">{t("music.tracks.hidden")}</p>}
      {!items.length && (
        <EmptyState icon="list" title={t("music.playlists.emptyList")}>
          <p>{t("music.playlists.emptyListText")}</p>
        </EmptyState>
      )}
      {items.length > 0 && (
        <TrackList
          rows={rows}
          label={t("music.tracks.label", { name: list.name })}
          playing={playing}
          onPlay={(i) => player.playItems(queueItems(), i)}
          menu={rowMenu}
          onKey={onKey}
        />
      )}
    </main>
  );
}

function ListRoute({ params }: { params: Record<string, string> }): JSX.Element {
  const id = params.id ?? "";
  return <List key={id} id={id} params={params} />;
}

export default function PlaylistPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <ListRoute params={params} />}</MediaGate>;
}
