// Kleine regels voor de muziekpagina's (fase 5, M9): namen, nummers, de rij en de acties op
// afspeellijsten. Zonder React, zodat tracks.test.ts ze kan nalopen.

import { errorText, t } from "../i18n";
import { addToPlaylist, createPlaylist } from "../media/api";
import type { AlbumCard, Playlist, Track } from "../media/types";
import { PLAYLIST_MAX } from "../media/types";
import { prompt } from "../ui/dialogs";
import { toast } from "../ui/toast";
import type { QueueInput } from "./queue";

/** De albumartiest van een compilatie, zoals de server hem bewaart (app/media/music.py VARIOUS). */
export const VARIOUS = "Diverse artiesten";

/** De naam van een artiest voor de UI: "Diverse artiesten" in de taal van de app. */
export function artistName(name: string | null | undefined): string {
  if (!name) return t("music.unknownArtist");
  return name === VARIOUS ? t("music.various") : name;
}

/** Een nummer voor de rij (met de titel en hoes van het album als die er zijn). */
export function toQueue(track: Track, album?: Pick<AlbumCard, "title" | "cover"> | null): QueueInput {
  return {
    node_id: track.node_id,
    title: track.title,
    artist: track.artist,
    album: album?.title ?? null,
    album_id: track.album_id,
    duration_ms: track.duration_ms,
    cover: album?.cover ?? null,
  };
}

/** Meer dan één schijf? Dan krijgt elk nummer ook zijn schijf. */
export function multiDisc(tracks: readonly Pick<Track, "disc">[]): boolean {
  return new Set(tracks.map((x) => x.disc ?? 1)).size > 1;
}

/** Het nummer zoals de lijst het toont: "3", of "2-03" bij meer schijven. */
export function trackLabel(track: Pick<Track, "disc" | "track">, multi: boolean, i: number): string {
  const n = track.track ?? i + 1;
  return multi ? `${track.disc ?? 1}-${String(n).padStart(2, "0")}` : String(n);
}

/** De duur van een rij nummers in ms (zonder duur telt als 0). */
export function totalMs(tracks: readonly Pick<Track, "duration_ms">[]): number {
  return tracks.reduce((sum, x) => sum + (x.duration_ms ?? 0), 0);
}

/** "ABBA · 1992": de artiest en het jaar van een album. */
export function albumSub(card: Pick<AlbumCard, "album_artist" | "year">): string {
  return [artistName(card.album_artist), card.year ? String(card.year) : ""].filter(Boolean).join(" · ");
}

/** Hooguit zoveel tekens in de naam van een afspeellijst (PlaylistIn op de server). */
export const NAME_MAX = 200;

/** Een naam voor een afspeellijst: niet leeg, hooguit NAME_MAX tekens. */
export function validName(v: string): string | null {
  const name = v.trim();
  if (!name) return t("common.dialog.required");
  return name.length > NAME_MAX ? t("music.playlists.tooLong", { max: NAME_MAX }) : null;
}

function fullOf(e: unknown): number | null {
  const err = e as { code?: unknown; params?: { max?: unknown } } | null;
  if (!err || err.code !== "media_playlist_full") return null;
  const max = Number(err.params?.max);
  return Number.isFinite(max) && max > 0 ? max : PLAYLIST_MAX;
}

/** Nummers in een afspeellijst, met een melding. Geeft true als het lukte. */
export async function addTracksTo(list: Pick<Playlist, "id" | "name">, nodeIds: readonly string[]): Promise<boolean> {
  if (!nodeIds.length) return false;
  try {
    const out = await addToPlaylist(list.id, { node_ids: [...nodeIds] });
    toast(t("music.playlists.added", { n: out.items.length, name: list.name }), { kind: "ok" });
    return true;
  } catch (e) {
    const max = fullOf(e);
    toast(max !== null ? t("music.playlists.full", { name: list.name, max }) : errorText(e), { kind: "error" });
    return false;
  }
}

/** Een nieuwe afspeellijst (de naam via een dialoog), eventueel meteen met nummers erin. */
export async function newPlaylist(nodeIds: readonly string[] = []): Promise<Playlist | null> {
  const name = await prompt({
    title: t("music.playlists.new"),
    label: t("music.playlists.name"),
    confirm: t("music.playlists.new"),
    validate: validName,
  });
  if (name === null) return null;
  try {
    const list = await createPlaylist(name.trim());
    toast(t("music.playlists.created", { name: list.name }), { kind: "ok" });
    if (nodeIds.length) await addTracksTo(list, nodeIds);
    return list;
  } catch (e) {
    toast(errorText(e), { kind: "error" });
    return null;
  }
}
