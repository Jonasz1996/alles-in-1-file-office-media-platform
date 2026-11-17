// De kleine regels van de muziekpagina's: namen, nummers, de rij, afspeellijsten (met een
// volle lijst), welke weg een sessie neemt (run of <audio src>) en de helpers van de pagina's.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { loadAreas, resetI18nForTests, t } from "../i18n";
import type { Artist, PlayOut, Track } from "../media/types";

const api = vi.hoisted(() => ({ addToPlaylist: vi.fn(), createPlaylist: vi.fn() }));
const toasts = vi.hoisted(() => [] as { text: string; kind?: string }[]);
vi.mock("../media/api", async (orig) => ({ ...(await orig<typeof import("../media/api")>()), ...api }));
vi.mock("../ui/toast", async (orig) => ({
  ...(await orig<typeof import("../ui/toast")>()),
  toast: (text: string, opts?: { kind?: string }) => toasts.push({ text, kind: opts?.kind }),
}));

import { findArtistKey } from "./AlbumPage";
import { parseSort, parseTab } from "./MusicHome";
import { moved } from "./PlaylistPage";
import { fileUrl, runKind } from "./player";
import {
  addTracksTo,
  albumSub,
  artistName,
  multiDisc,
  NAME_MAX,
  toQueue,
  totalMs,
  trackLabel,
  VARIOUS,
  validName,
} from "./tracks";

async function inLocale(locale: "nl" | "en") {
  resetI18nForTests(locale);
  await loadAreas(["music"]);
}

beforeAll(() => inLocale("nl"));
afterEach(() => inLocale("nl"));
beforeEach(() => {
  toasts.length = 0;
  api.addToPlaylist.mockReset();
});

const track = (over: Partial<Track> = {}): Track => ({
  node_id: "n1",
  title: "Waterloo",
  artist: "ABBA",
  album_id: "a1",
  disc: 1,
  track: 1,
  duration_ms: 165_000,
  ...over,
});

describe("namen en nummers", () => {
  it("de compilatie-artiest en een onbekende artiest in de taal van de app", async () => {
    expect(artistName("ABBA")).toBe("ABBA");
    expect(artistName(VARIOUS)).toBe(t("music.various"));
    expect(artistName(null)).toBe(t("music.unknownArtist"));
    await inLocale("en");
    expect(artistName(VARIOUS)).toBe("Various artists");
    expect(albumSub({ album_artist: "ABBA", year: 1974 })).toBe("ABBA · 1974");
    expect(albumSub({ album_artist: "ABBA", year: null })).toBe("ABBA");
  });

  it("meer schijven: het nummer met de schijf ervoor", () => {
    const one = [track(), track({ track: 2 })];
    const two = [track(), track({ disc: 2, track: 3 })];
    expect(multiDisc(one)).toBe(false);
    expect(multiDisc(two)).toBe(true);
    expect(multiDisc([track({ disc: null }), track({ disc: 1 })])).toBe(false);
    expect(trackLabel(two[1], true, 1)).toBe("2-03");
    expect(trackLabel(one[1], false, 1)).toBe("2");
    expect(trackLabel(track({ track: null }), false, 4)).toBe("5");
    expect(totalMs([track(), track({ duration_ms: null }), track({ duration_ms: 5 })])).toBe(165_005);
  });

  it("een nummer voor de rij neemt de titel en hoes van het album mee", () => {
    expect(toQueue(track(), { title: "Waterloo", cover: "/c" })).toEqual({
      node_id: "n1",
      title: "Waterloo",
      artist: "ABBA",
      album: "Waterloo",
      album_id: "a1",
      duration_ms: 165_000,
      cover: "/c",
    });
    expect(toQueue(track()).album).toBeNull();
  });
});

describe("afspeellijsten", () => {
  it("de naam: niet leeg en niet te lang", () => {
    expect(validName("  ")).toBe(t("common.dialog.required"));
    expect(validName("x".repeat(NAME_MAX))).toBeNull();
    expect(validName("x".repeat(NAME_MAX + 1))).toBe(t("music.playlists.tooLong", { max: NAME_MAX }));
  });

  it("toevoegen: een melding, en bij een volle lijst het maximum van de server", async () => {
    api.addToPlaylist.mockResolvedValueOnce({ items: [{}, {}] });
    expect(await addTracksTo({ id: "p1", name: "Feest" }, ["n1", "n2"])).toBe(true);
    expect(api.addToPlaylist).toHaveBeenCalledWith("p1", { node_ids: ["n1", "n2"] });
    expect(toasts.at(-1)).toEqual({ text: t("music.playlists.added", { n: 2, name: "Feest" }), kind: "ok" });

    api.addToPlaylist.mockRejectedValueOnce(
      Object.assign(new Error("vol"), { code: "media_playlist_full", status: 409, params: { max: 5000 } }),
    );
    expect(await addTracksTo({ id: "p1", name: "Feest" }, ["n3"])).toBe(false);
    expect(toasts.at(-1)).toEqual({ text: t("music.playlists.full", { name: "Feest", max: 5000 }), kind: "error" });

    expect(await addTracksTo({ id: "p1", name: "Feest" }, [])).toBe(false);
    expect(api.addToPlaylist).toHaveBeenCalledTimes(2);
  });

  it("verplaatsen in een lijst (de volgorde voor de server)", () => {
    expect(moved(["a", "b", "c", "d"], 0, 2)).toEqual(["b", "c", "a", "d"]);
    expect(moved(["a", "b", "c", "d"], 3, 0)).toEqual(["d", "a", "b", "c"]);
    expect(moved(["a", "b"], 0, 5)).toEqual(["a", "b"]);
    expect(moved(["a", "b"], 1, 1)).toEqual(["a", "b"]);
  });
});

describe("de pagina's", () => {
  it("tab en sortering uit het adres, met een veilige standaard", () => {
    expect(parseTab("lijsten")).toBe("lijsten");
    expect(parseTab("<script>")).toBe("albums");
    expect(parseTab(null)).toBe("albums");
    expect(parseSort("year")).toBe("year");
    expect(parseSort("__proto__")).toBe("title");
  });

  it("de artiest van een album: eerst precies, dan zonder hoofdletters", () => {
    const artists = [
      { key: "k1", name: "ABBA" },
      { key: "k2", name: "abba" },
      { key: "k3", name: "Björk" },
    ] as Artist[];
    expect(findArtistKey(artists, "abba")).toBe("k2");
    expect(findArtistKey(artists, "BJÖRK")).toBe("k3");
    expect(findArtistKey(artists, "Queen")).toBeNull();
  });
});

describe("welke weg een sessie neemt", () => {
  const out = (over: Partial<PlayOut>): Pick<PlayOut, "sid" | "mode" | "urls" | "mime"> => ({
    sid: "song.flac.AbC",
    mode: "audio",
    urls: { audio: "audio.mp4" } as PlayOut["urls"],
    mime: 'audio/mp4; codecs="flac"',
    ...over,
  });
  const all = () => true;
  const none = () => false;

  it("mode audio: de run met audio.mp4 als MSE het type kent", () => {
    expect(runKind(out({}), all)).toEqual({
      url: "/api/v1/stream/song.flac.AbC/audio.mp4",
      mime: 'audio/mp4; codecs="flac"',
      sequence: false,
    });
    expect(runKind(out({}), none)).toBeNull();
  });

  it("MP3 direct: de run in mode sequence; ander direct: <audio src>", () => {
    const mp3 = out({ mode: "direct", urls: { file: "file" } as PlayOut["urls"], mime: "audio/mpeg" });
    expect(runKind(mp3, all)).toEqual({ url: "/api/v1/stream/song.flac.AbC/file", mime: "audio/mpeg", sequence: true });
    expect(runKind(mp3, none)).toBeNull();
    const wav = out({ mode: "direct", urls: { file: "file" } as PlayOut["urls"], mime: "audio/wav" });
    expect(runKind(wav, all)).toBeNull();
    expect(fileUrl(wav)).toBe("/api/v1/stream/song.flac.AbC/file");
    expect(fileUrl(out({}))).toBe("/api/v1/stream/song.flac.AbC/audio.mp4");
    expect(fileUrl(out({ urls: {} as PlayOut["urls"] }))).toBeNull();
  });
});
