// /muziek (fase 5, M9; Keuze 37): albums, artiesten en afspeellijsten in drie tabbladen
// (?tab=albums|artiesten|lijsten). Albums en artiesten zijn per muziekbibliotheek (?bib=, de
// eerste als je niets kiest); afspeellijsten zijn van jou, over alle bibliotheken.
//
// Albums komen per pagina (60, "Meer laden"); een media.library-gebeurtenis van de bibliotheek
// (de scan, een nieuwe groepering) laadt ze opnieuw.

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { useReloadOn } from "../events";
import { errorText, t } from "../i18n";
import { Link } from "../links";
import { artistPath, MEDIA_HOME, playlistPath, useMediaStatus } from "../media";
import { listAlbums, listArtists } from "../media/api";
import { MediaGate } from "../media/Gate";
import { affectsLibrary } from "../media/library";
import type { AlbumCard, Artist, Library } from "../media/types";
import type { RouteProps } from "../router";
import { useQueryParam } from "../router";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { Tabs, tabPanelProps } from "../ui/Tabs";
import { AlbumGrid, LinkList, playlistActions, playlistMeta, useMusicOwner, usePlaylists } from "./parts";
import { artistName, newPlaylist } from "./tracks";

const TABS = ["albums", "artiesten", "lijsten"] as const;
type Tab = (typeof TABS)[number];
const SORTS = ["title", "year", "added"] as const;
type Sort = (typeof SORTS)[number];

export function parseTab(v: string | null): Tab {
  return (TABS as readonly string[]).includes(v ?? "") ? (v as Tab) : "albums";
}

export function parseSort(v: string | null): Sort {
  return (SORTS as readonly string[]).includes(v ?? "") ? (v as Sort) : "title";
}

const TAB_LABEL: Record<Tab, "music.tabs.albums" | "music.tabs.artists" | "music.tabs.playlists"> = {
  albums: "music.tabs.albums",
  artiesten: "music.tabs.artists",
  lijsten: "music.tabs.playlists",
};

// --- albums ---

interface AlbumList {
  key: string;
  items: AlbumCard[];
  next: string | null;
  loading: boolean;
  loaded: boolean;
  error: string | null;
}

function emptyAlbums(key: string): AlbumList {
  return { key, items: [], next: null, loading: true, loaded: false, error: null };
}

function Albums({ lib }: { lib: Library }): JSX.Element {
  const [sortParam, setSortParam] = useQueryParam("sort");
  const sort = parseSort(sortParam);
  const key = `${lib.id}|${sort}`;
  const [state, setState] = useState<AlbumList>(() => emptyAlbums(key));
  const gen = useRef(0);

  const first = useCallback(async () => {
    const g = ++gen.current;
    try {
      const page = await listAlbums(lib.id, { sort });
      if (g === gen.current) setState({ key, items: page.items, next: page.next, loading: false, loaded: true, error: null });
    } catch (e) {
      if (g === gen.current) setState((old) => ({ ...old, key, loading: false, loaded: true, error: errorText(e) }));
    }
  }, [lib.id, sort, key]);

  useEffect(() => {
    setState(emptyAlbums(key));
    void first();
  }, [first, key]);

  useReloadOn((e) => affectsLibrary(e, lib.id), () => void first(), 500);

  const more = async () => {
    if (!state.next || state.loading) return;
    const g = gen.current;
    setState((old) => ({ ...old, loading: true, error: null }));
    try {
      const page = await listAlbums(lib.id, { sort, cursor: state.next });
      if (g !== gen.current) return;
      setState((old) => {
        const seen = new Set(old.items.map((a) => a.id));
        return { ...old, items: [...old.items, ...page.items.filter((a) => !seen.has(a.id))], next: page.next, loading: false };
      });
    } catch (e) {
      if (g === gen.current) setState((old) => ({ ...old, loading: false, error: errorText(e) }));
    }
  };

  const list = state.key === key ? state : emptyAlbums(key);
  return (
    <>
      <div className="music-toolbar">
        <label>
          <span>{t("music.sort.label")}</span>
          <select
            value={sort}
            onChange={(e) => setSortParam(e.target.value === "title" ? null : e.target.value, { replace: true })}
          >
            {SORTS.map((s) => (
              <option key={s} value={s}>
                {t(`music.sort.${s}`)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!list.loaded && <Skeleton lines={3} />}
      {list.loaded && !list.items.length && !list.error && (
        <EmptyState icon="audio" title={t("music.albums.empty")}>
          <p>{t("music.albums.emptyText")}</p>
        </EmptyState>
      )}
      {list.items.length > 0 && <AlbumGrid albums={list.items} label={t("music.albums.label")} />}
      {list.next && !list.error && (
        <p className="music-more">
          <Button busy={list.loading} onClick={() => void more()}>
            {t("music.more")}
          </Button>
        </p>
      )}
      {list.error && (
        <div role="alert" className="music-error">
          <p>{list.error}</p>
          <Button onClick={() => (list.items.length ? void more() : void first())}>{t("common.retry")}</Button>
        </div>
      )}
    </>
  );
}

// --- artiesten ---

function Artists({ lib }: { lib: Library }): JSX.Element {
  const [artists, setArtists] = useState<Artist[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const gen = useRef(0);
  const load = useCallback(() => {
    const g = ++gen.current;
    listArtists(lib.id).then(
      (x) => {
        if (g !== gen.current) return;
        setArtists(x);
        setError(null);
      },
      (e: unknown) => {
        if (g === gen.current) setError(errorText(e));
      },
    );
  }, [lib.id]);
  useEffect(() => {
    setArtists(null);
    load();
  }, [load]);
  useReloadOn((e) => affectsLibrary(e, lib.id), load, 500);

  if (error) {
    return (
      <div role="alert" className="music-error">
        <p>{error}</p>
        <Button onClick={load}>{t("common.retry")}</Button>
      </div>
    );
  }
  if (!artists) return <Skeleton lines={3} />;
  if (!artists.length) return <EmptyState icon="user" title={t("music.artists.empty")} />;
  return (
    <LinkList
      items={artists}
      label={t("music.artists.label")}
      keyOf={(a) => a.key}
      hrefOf={(a) => `${artistPath(a.key)}?bib=${encodeURIComponent(lib.id)}`}
      render={(a) => (
        <>
          <span className="music-list-title">{artistName(a.name)}</span>
          <span className="muted">{t("music.albums.count", { n: a.album_count })}</span>
        </>
      )}
    />
  );
}

// --- afspeellijsten ---

function Playlists(): JSX.Element {
  const { lists, reload } = usePlaylists();
  return (
    <>
      <div className="music-toolbar">
        <Button icon="list" onClick={() => void newPlaylist().then((x) => x && reload())} data-new-playlist="">
          {t("music.playlists.new")}
        </Button>
      </div>
      {!lists && <Skeleton lines={3} />}
      {lists && !lists.length && (
        <EmptyState icon="list" title={t("music.playlists.empty")}>
          <p>{t("music.playlists.emptyText")}</p>
        </EmptyState>
      )}
      {lists && lists.length > 0 && (
        <LinkList
          items={lists}
          label={t("music.playlists.label")}
          keyOf={(p) => p.id}
          hrefOf={(p) => playlistPath(p.id)}
          render={(p) => (
            <>
              <span className="music-list-title">{p.name}</span>
              <span className="muted">{playlistMeta(p)}</span>
            </>
          )}
          menu={(p) => playlistActions(p, reload, reload)}
          menuLabel={(p) => t("music.playlists.more", { name: p.name })}
        />
      )}
    </>
  );
}

// --- de pagina ---

function Music(): JSX.Element {
  useMusicOwner();
  const status = useMediaStatus();
  const libs = (status?.libraries ?? []).filter((l) => l.kind === "music");
  const [tabParam, setTab] = useQueryParam("tab");
  const [bibParam, setBib] = useQueryParam("bib");
  const tab = parseTab(tabParam);
  const lib = libs.find((l) => l.id === bibParam) ?? libs[0] ?? null;

  return (
    <main className="wide music-page">
      <PageTitle>{t("music.title")}</PageTitle>
      <Tabs
        label={t("music.tabs.label")}
        idPrefix="music"
        current={tab}
        onSelect={(id) => setTab(id === "albums" ? null : id, { replace: true })}
        tabs={TABS.map((id) => ({ id, label: t(TAB_LABEL[id]) }))}
      />
      <section {...tabPanelProps("music", tab)} className="music-panel">
        {tab !== "lijsten" && libs.length > 1 && lib && (
          <div className="music-toolbar">
            <label>
              <span>{t("music.library")}</span>
              <select value={lib.id} onChange={(e) => setBib(e.target.value, { replace: true })}>
                {libs.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        {tab !== "lijsten" && !lib && (
          <EmptyState icon="audio" title={t("music.noLibrary.title")}>
            <p>{t("music.noLibrary.text")}</p>
            <Link to={MEDIA_HOME} className="btn">
              {t("music.noLibrary.link")}
            </Link>
          </EmptyState>
        )}
        {tab === "albums" && lib && <Albums key={lib.id} lib={lib} />}
        {tab === "artiesten" && lib && <Artists key={lib.id} lib={lib} />}
        {tab === "lijsten" && <Playlists />}
      </section>
    </main>
  );
}

export default function MusicHome({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <Music />}</MediaGate>;
}
