// Gedeelde onderdelen van de muziekpagina's (fase 5, M9): de hoes, het raster van albums, de
// lijst van nummers, het menu per regel en de keuze "Aan afspeellijst toevoegen".
//
// Toetsenbord: elke lijst is één tabstop. De pijltjes, Home/End en PageUp/PageDown verplaatsen
// de focus (ui/virtual useRovingFocus); Enter speelt of opent. In de regel met de focus zijn ook
// de knoppen ernaast een tabstop (Meer), in de andere niet. Shift+F10 of de menutoets opent het
// menu van de regel. De lijst van nummers is gevirtualiseerd (een afspeellijst kan 5.000
// nummers hebben).

import {
  type JSX,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useCurrentUser } from "../currentUser";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import { albumPath } from "../media";
import { deletePlaylist, listPlaylists, renamePlaylist } from "../media/api";
import type { AlbumCard, Playlist, Track } from "../media/types";
import { confirm, prompt } from "../ui/dialogs";
import { Icon } from "../ui/Icon";
import { Menu, type MenuItem } from "../ui/Menu";
import { toast } from "../ui/toast";
import { useColumns, useRovingFocus, useVirtualRows } from "../ui/virtual";
import { player } from "./player";
import { type QueueInput, queue } from "./queue";
import { addTracksTo, albumSub, newPlaylist, validName } from "./tracks";
import "./music.css";

/** De rij en de speler horen bij de ingelogde gebruiker (een rij uit sessionStorage van een
 * ander komt niet terug). Elke muziekpagina en de balk roepen dit aan. */
export function useMusicOwner(): void {
  const user = useCurrentUser();
  const id = user?.id ?? null;
  useEffect(() => {
    player.bind(id);
  }, [id]);
}

/** Het nummer dat nu speelt (node-id), of null. */
export function usePlayingNode(): string | null {
  const q = useSyncExternalStore(queue.subscribe, queue.snapshot, queue.snapshot);
  const state = useSyncExternalStore(player.subscribe, player.snapshot, player.snapshot);
  if (state.status === "idle") return null;
  return q.items[q.index]?.node_id ?? null;
}

/** In de rij zetten, met een melding (hoeveel, of: de rij is vol). */
export function enqueueWithToast(items: readonly QueueInput[]): void {
  const added = player.enqueue(items);
  if (added) toast(t("music.actions.queued", { n: added }), { kind: "ok" });
  else toast(t("music.actions.queueFull"), { kind: "error" });
}

// --- de hoes ---

export function Cover({ src, size = 32 }: { src: string | null | undefined; size?: 16 | 20 | 24 | 32 }): JSX.Element {
  return (
    <span className="music-cover" aria-hidden="true">
      {src ? <img src={src} alt="" loading="lazy" decoding="async" /> : <Icon name="audio" size={size} />}
    </span>
  );
}

// --- het raster van albums ---

const CARD_MIN = 150;
const CARD_GAP = 16;

function focusIndex(root: HTMLElement | null, i: number): HTMLElement | null {
  return root?.querySelector<HTMLElement>(`[data-index="${i}"]`) ?? null;
}

/** Albums als raster van links (één tabstop, de pijltjes in twee richtingen). */
export function AlbumGrid({ albums, label }: { albums: readonly AlbumCard[]; label: string }): JSX.Element {
  const ref = useRef<HTMLUListElement>(null);
  const { columns } = useColumns(ref, CARD_MIN, CARD_GAP);
  const [focus, setFocus] = useState(0);
  const current = Math.min(focus, Math.max(0, albums.length - 1));
  const onKeyDown = useRovingFocus({
    count: albums.length,
    index: current,
    setIndex: setFocus,
    columns,
    scrollToIndex: (i) => focusIndex(ref.current, i)?.scrollIntoView({ block: "nearest" }),
    getElement: (i) => focusIndex(ref.current, i),
  });
  return (
    <ul
      ref={ref}
      className="music-grid"
      aria-label={label}
      onKeyDown={onKeyDown}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {albums.map((a, i) => (
        <li key={a.id}>
          <Link
            to={albumPath(a.id)}
            className="music-card"
            data-index={i}
            tabIndex={i === current ? 0 : -1}
            onFocus={() => setFocus(i)}
          >
            <Cover src={a.cover} size={32} />
            <span className="music-card-title">{a.title}</span>
            <span className="music-card-sub muted">{albumSub(a)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

// --- een lijst met regels (artiesten, afspeellijsten) ---

/** Een lijst van links met één tabstop; per regel eventueel een menu (alleen in de regel met de
 * focus een tabstop). */
export function LinkList<T>({
  items,
  label,
  keyOf,
  hrefOf,
  render,
  menu,
  menuLabel,
}: {
  items: readonly T[];
  label: string;
  keyOf(item: T): string;
  hrefOf(item: T): string;
  render(item: T): ReactNode;
  menu?(item: T): readonly MenuItem[];
  menuLabel?(item: T): string;
}): JSX.Element {
  const ref = useRef<HTMLUListElement>(null);
  const [focus, setFocus] = useState(0);
  const current = Math.min(focus, Math.max(0, items.length - 1));
  const roving = useRovingFocus({
    count: items.length,
    index: current,
    setIndex: setFocus,
    scrollToIndex: (i) => focusIndex(ref.current, i)?.scrollIntoView({ block: "nearest" }),
    getElement: (i) => focusIndex(ref.current, i),
  });
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if ((e.target as HTMLElement).dataset.index === undefined) return;
    roving(e);
  };
  return (
    <ul ref={ref} className="music-list" aria-label={label} onKeyDown={onKeyDown}>
      {items.map((item, i) => (
        <li key={keyOf(item)} className="music-list-row">
          <Link
            to={hrefOf(item)}
            className="music-list-link"
            data-index={i}
            tabIndex={i === current ? 0 : -1}
            onFocus={() => setFocus(i)}
            onKeyDown={(e) => openMenuKey(e)}
          >
            {render(item)}
          </Link>
          {menu && menuLabel && (
            <RowMenu label={menuLabel(item)} items={() => menu(item)} tabbable={i === current} onFocus={() => setFocus(i)} />
          )}
        </li>
      ))}
    </ul>
  );
}

/** Shift+F10 of de menutoets op een regel: het menu van die regel (de knop ernaast). */
function openMenuKey(e: KeyboardEvent<HTMLElement>): void {
  if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
    const button = (e.currentTarget.parentElement ?? e.currentTarget).querySelector<HTMLButtonElement>(
      "button[aria-haspopup='menu']",
    );
    if (!button) return;
    e.preventDefault();
    button.click();
  }
}

// --- het menu van een regel ---

/** Zoals ui/Menu MenuButton, maar met een tabIndex die de lijst bepaalt (roving). */
export function RowMenu({
  label,
  items,
  tabbable,
  onFocus,
}: {
  label: string;
  items(): readonly MenuItem[];
  tabbable: boolean;
  onFocus?(): void;
}): JSX.Element {
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState<{ items: readonly MenuItem[]; focus: "first" | "last" } | null>(null);
  const show = (focus: "first" | "last") => setOpen({ items: items(), focus });
  return (
    <>
      <button
        ref={button}
        type="button"
        className="icon-button menu-button music-row-menu"
        aria-haspopup="menu"
        aria-expanded={open !== null}
        aria-label={label}
        title={label}
        tabIndex={tabbable ? 0 : -1}
        onFocus={onFocus}
        onClick={() => (open ? setOpen(null) : show("first"))}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            e.stopPropagation();
            show(e.key === "ArrowDown" ? "first" : "last");
          }
        }}
      >
        <Icon name="more" size={20} />
      </button>
      {open && button.current && (
        <Menu
          items={open.items}
          anchor={button.current}
          label={label}
          initialFocus={open.focus}
          onClose={(reason) => {
            setOpen(null);
            if (reason !== "outside") button.current?.focus();
          }}
        />
      )}
    </>
  );
}

// --- afspeellijsten in een menu ---

/** De afspeellijsten van de gebruiker (voor het menu "Aan afspeellijst toevoegen"). */
export function usePlaylists(active = true): { lists: Playlist[] | null; reload(): void } {
  const [lists, setLists] = useState<Playlist[] | null>(null);
  const gen = useRef(0);
  const reload = useCallback(() => {
    const g = ++gen.current;
    listPlaylists().then(
      (x) => g === gen.current && setLists(x),
      () => g === gen.current && setLists([]),
    );
  }, []);
  useEffect(() => {
    if (active) reload();
  }, [active, reload]);
  return { lists, reload };
}

/** Het item "Aan afspeellijst toevoegen" met een submenu: elke lijst en "Nieuwe afspeellijst…". */
export function addToMenu(nodeIds: () => string[], lists: readonly Playlist[] | null, changed: () => void): MenuItem {
  const items: MenuItem[] = (lists ?? []).map((list) => ({
    id: `list-${list.id}`,
    label: list.name,
    onSelect: () => void addTracksTo(list, nodeIds()).then((ok) => ok && changed()),
  }));
  items.push({
    id: "new-list",
    label: t("music.actions.newList"),
    separatorBefore: items.length > 0,
    onSelect: () => void newPlaylist(nodeIds()).then((list) => list && changed()),
  });
  return { id: "add-to", label: t("music.actions.addTo"), icon: "list", items };
}

// --- een afspeellijst: tekst en acties ---

export function playlistMeta(p: Pick<Playlist, "item_count" | "duration_ms">): string {
  const parts = [t("music.tracks.count", { n: p.item_count })];
  if (p.duration_ms > 0) parts.push(fmt.duration(Math.round(p.duration_ms / 1000)));
  return parts.join(" · ");
}

/** Naam wijzigen en verwijderen (ook op de pagina van de lijst). */
export function playlistActions(list: Playlist, changed: () => void, deleted: () => void): MenuItem[] {
  return [
    {
      id: "rename",
      label: t("music.playlists.rename"),
      icon: "rename",
      onSelect: () =>
        void (async () => {
          const name = await prompt({
            title: t("music.playlists.rename"),
            label: t("music.playlists.name"),
            initial: list.name,
            confirm: t("music.playlists.rename"),
            validate: validName,
          });
          if (name === null || name.trim() === list.name) return;
          try {
            const out = await renamePlaylist(list.id, name.trim());
            toast(t("music.playlists.renamed", { name: out.name }), { kind: "ok" });
            changed();
          } catch (e) {
            toast(errorText(e), { kind: "error" });
          }
        })(),
    },
    {
      id: "delete",
      label: t("music.playlists.delete"),
      icon: "trash",
      danger: true,
      onSelect: () =>
        void (async () => {
          const ok = await confirm({
            title: t("music.playlists.deleteTitle"),
            message: t("music.playlists.deleteText", { name: list.name }),
            confirm: t("music.playlists.delete"),
            danger: true,
          });
          if (!ok) return;
          try {
            await deletePlaylist(list.id);
            toast(t("music.playlists.deleted", { name: list.name }), { kind: "ok" });
            deleted();
          } catch (e) {
            toast(errorText(e), { kind: "error" });
          }
        })(),
    },
  ];
}

// --- de lijst van nummers ---

export const TRACK_ROW_H = 52;

export interface TrackRow {
  key: string;
  track: Track;
  /** Wat in de eerste kolom staat ("3", "2-03", of de plek in de afspeellijst) */
  number: string;
}

/** Nummers als gevirtualiseerde lijst: Enter of klik speelt vanaf dat nummer; Meer opent het
 * menu. onKey: extra toetsen op een regel (bij een afspeellijst Alt+pijl en Delete). */
export function TrackList({
  rows,
  label,
  playing,
  onPlay,
  menu,
  onKey,
}: {
  rows: readonly TrackRow[];
  label: string;
  /** Het node-id dat nu speelt */
  playing: string | null;
  onPlay(i: number): void;
  menu(i: number): readonly MenuItem[];
  onKey?(e: KeyboardEvent<HTMLElement>, i: number, focus: (i: number) => void): boolean;
}): JSX.Element {
  const body = useRef<HTMLDivElement>(null);
  const [focus, setFocus] = useState(0);
  const current = Math.min(focus, Math.max(0, rows.length - 1));
  const virtual = useVirtualRows({
    count: rows.length,
    rowHeight: TRACK_ROW_H,
    overscan: 8,
    listRef: body,
    keepIndex: rows.length ? current : null,
  });
  const element = useCallback((i: number) => focusIndex(body.current, i), []);
  const roving = useRovingFocus({
    count: rows.length,
    index: current,
    setIndex: setFocus,
    scrollToIndex: (i) => virtual.scrollToIndex(i),
    getElement: element,
  });
  const focusRow = useCallback(
    (i: number) => {
      setFocus(i);
      virtual.scrollToIndex(i);
      requestAnimationFrame(() => element(i)?.focus({ preventScroll: true }));
    },
    [virtual, element],
  );
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const index = (e.target as HTMLElement).dataset.index;
    if (index === undefined) return;
    if (onKey?.(e, Number(index), focusRow)) {
      e.preventDefault();
      return;
    }
    roving(e);
  };

  const out: JSX.Element[] = [];
  const add = (i: number) => {
    const row = rows[i];
    const tr = row.track;
    const now = playing !== null && tr.node_id === playing;
    out.push(
      <div
        key={row.key}
        role="listitem"
        className={now ? "music-track now" : "music-track"}
        style={{ transform: `translateY(${virtual.offsetOf(i)}px)`, height: TRACK_ROW_H }}
      >
        <button
          type="button"
          className="music-track-main"
          data-index={i}
          tabIndex={i === current ? 0 : -1}
          aria-current={now ? "true" : undefined}
          aria-label={t("music.actions.playTrack", { title: tr.title })}
          onFocus={() => setFocus(i)}
          onClick={() => onPlay(i)}
          onKeyDown={(e) => openMenuKey(e)}
        >
          <span className="music-track-no muted">{now ? <Icon name="play" size={16} /> : row.number}</span>
          <span className="music-track-text">
            <span className="music-track-title">{tr.title}</span>
            {tr.artist && <span className="music-track-artist muted">{tr.artist}</span>}
          </span>
          <span className="music-track-time muted">
            {tr.duration_ms ? fmt.duration(Math.round(tr.duration_ms / 1000)) : ""}
          </span>
        </button>
        <RowMenu
          label={t("music.actions.more", { title: tr.title })}
          items={() => menu(i)}
          tabbable={i === current}
          onFocus={() => setFocus(i)}
        />
      </div>,
    );
  };
  for (let i = virtual.start; i < virtual.end; i++) add(i);
  if (virtual.extra !== null) add(virtual.extra);

  return (
    <div className="music-tracks" role="list" aria-label={label} onKeyDown={onKeyDown}>
      <div ref={body} className="music-tracks-body" style={{ height: virtual.totalHeight }}>
        {out}
      </div>
    </div>
  );
}
