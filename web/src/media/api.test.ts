import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import * as media from "./api";
import { StatusStore } from "./index";
import type { ErrorCatalog } from "../i18n/types";
import { type ClientProfile, MEDIA_ERROR_CODES, MEDIA_ERROR_STATUS, MEDIA_NOTICES, type PlayIn } from "./types";

// De catalogus van M0-api (i18n/errors/media.ts); leeg zolang die er in deze worktree niet is
const catalogs = import.meta.glob<ErrorCatalog>("../i18n/errors/media.ts", { eager: true, import: "default" });

afterEach(() => {
  vi.restoreAllMocks();
});

const profile: ClientProfile = {
  v: 1,
  base: "web",
  client: { kind: "web", name: "Chrome 141", app: "0.7.0" },
  engines: { mse: true, mms: false, native_hls: false },
  file: ["video/webm"],
  codecs: { "vp09.00.40.08": { mse: true, file: true, smooth: true } },
  hdr: { pq: false, hlg: false, dv: false, screen: false },
  max: { height: 1080, bitrate_kbps: 0 },
  audio: { channels: 2, track_switch: false },
};

describe("de aanroepen van §2 (M2, M3, M7, M8, M9, M10)", () => {
  it("elke functie gebruikt de methode en het pad van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    const body: PlayIn = { profile, quality: 720, start_ms: 5000, replace: "x.y" };
    // M2
    await media.listLibraries();
    await media.createLibrary({ root_id: "map", kind: "movies" });
    await media.updateLibrary("l/1", { name: "Films" });
    await media.deleteLibrary("l1");
    await media.scanLibrary("l1");
    await media.getHome();
    await media.listItems("l1");
    await media.listItems("l1", { type: "show", sort: "added", cursor: "c", limit: 60 });
    await media.getItem("n1");
    await media.getShow("s1");
    // M3
    await media.getCodecs("n1");
    await media.play("n1", body);
    // M7
    await media.putProgress("n1", {
      position_ms: 1,
      duration_ms: 2,
      event: "tick",
      origin: "o",
      seq: 1,
      started_at: "2026-10-28T10:00:00Z",
    });
    await media.putProgress("n1", { position_ms: 1, duration_ms: 2, event: "stop", origin: "o", seq: 2, started_at: "t" }, { keepalive: true });
    await media.markWatched({ node_ids: ["a"], watched: true });
    await media.markWatched({ show_id: "s1", season: 2, watched: false });
    // M8
    await media.candidates("n1");
    await media.candidates("n1", "Alien", 1979);
    await media.matchItem("n1", { provider: "tmdb", id: "348" });
    await media.unmatchItem("n1");
    await media.updateItem("n1", { title: "Alien", unlock: ["year"] });
    await media.matchShow("s1", { provider: "tmdb", id: "1399" });
    // M9
    await media.listAlbums("l1");
    await media.listAlbums("l1", { sort: "year", cursor: "c2" });
    await media.getAlbum("a1");
    await media.listArtists("l1");
    await media.getArtist("the beatles");
    await media.getArtist("abba", "l1");
    await media.listPlaylists();
    await media.createPlaylist("Zondag");
    await media.getPlaylist("p1");
    await media.renamePlaylist("p1", "Maandag");
    await media.deletePlaylist("p1");
    await media.addToPlaylist("p1", { node_ids: ["t1", "t2"], position: 0 });
    await media.reorderPlaylist("p1", ["i2", "i1"]);
    await media.removeFromPlaylist("p1", "i1");
    // M10
    await media.getAdmin();
    await media.putConfig({ cpu_budget: 3, enabled: true });
    await media.startBench();
    await media.stopSession("u1");
    await media.retryFailed();
    await media.clearCache({ what: "trick" });
    await media.setTmdbKey({ key: "k" });
    await media.deleteTmdbKey();
    await media.testTmdb();
    await media.importSources();
    await media.startImport({ source: "Films", target_id: "map", owner_id: "u1", move: false });
    await media.startLoadtest({ streams: 3, seconds: 300, codec: "vp9" });
    await media.getLoadtest();

    const M = "/api/v1/media";
    const A = "/api/v1/admin/media";
    expect(request.mock.calls).toEqual([
      ["GET", `${M}/libraries`],
      ["POST", `${M}/libraries`, { root_id: "map", kind: "movies" }],
      ["PATCH", `${M}/libraries/l%2F1`, { name: "Films" }],
      ["DELETE", `${M}/libraries/l1`],
      ["POST", `${M}/libraries/l1/scan`],
      ["GET", `${M}/home`],
      ["GET", `${M}/libraries/l1/items`],
      ["GET", `${M}/libraries/l1/items?type=show&sort=added&cursor=c&limit=60`],
      ["GET", `${M}/items/n1`],
      ["GET", `${M}/shows/s1`],
      ["GET", `${M}/n1/codecs`],
      ["POST", `${M}/n1/play`, body],
      [
        "PUT",
        `${M}/progress/n1`,
        { position_ms: 1, duration_ms: 2, event: "tick", origin: "o", seq: 1, started_at: "2026-10-28T10:00:00Z" },
        {},
      ],
      [
        "PUT",
        `${M}/progress/n1`,
        { position_ms: 1, duration_ms: 2, event: "stop", origin: "o", seq: 2, started_at: "t" },
        { keepalive: true },
      ],
      ["POST", `${M}/progress/batch`, { node_ids: ["a"], watched: true }],
      ["POST", `${M}/progress/batch`, { show_id: "s1", season: 2, watched: false }],
      ["GET", `${M}/items/n1/candidates`],
      ["GET", `${M}/items/n1/candidates?q=Alien&year=1979`],
      ["POST", `${M}/items/n1/match`, { provider: "tmdb", id: "348" }],
      ["DELETE", `${M}/items/n1/match`],
      ["PATCH", `${M}/items/n1`, { title: "Alien", unlock: ["year"] }],
      ["POST", `${M}/shows/s1/match`, { provider: "tmdb", id: "1399" }],
      ["GET", `${M}/libraries/l1/albums`],
      ["GET", `${M}/libraries/l1/albums?sort=year&cursor=c2`],
      ["GET", `${M}/albums/a1`],
      ["GET", `${M}/libraries/l1/artists`],
      ["GET", `${M}/artists/the%20beatles`],
      ["GET", `${M}/artists/abba?library=l1`],
      ["GET", `${M}/playlists`],
      ["POST", `${M}/playlists`, { name: "Zondag" }],
      ["GET", `${M}/playlists/p1`],
      ["PATCH", `${M}/playlists/p1`, { name: "Maandag" }],
      ["DELETE", `${M}/playlists/p1`],
      ["POST", `${M}/playlists/p1/items`, { node_ids: ["t1", "t2"], position: 0 }],
      ["PUT", `${M}/playlists/p1/order`, { item_ids: ["i2", "i1"] }],
      ["DELETE", `${M}/playlists/p1/items/i1`],
      ["GET", A],
      ["PUT", `${A}/config`, { cpu_budget: 3, enabled: true }],
      ["POST", `${A}/bench`],
      ["DELETE", `${A}/sessions/u1`],
      ["POST", `${A}/retry`],
      ["POST", `${A}/cache/clear`, { what: "trick" }],
      ["PUT", `${A}/tmdb`, { key: "k" }],
      ["DELETE", `${A}/tmdb`],
      ["POST", `${A}/tmdb/test`],
      ["GET", `${A}/import/sources`],
      ["POST", `${A}/import`, { source: "Films", target_id: "map", owner_id: "u1", move: false }],
      ["POST", `${A}/loadtest`, { streams: 3, seconds: 300, codec: "vp9" }],
      ["GET", `${A}/loadtest`],
    ]);
  });

  it("publiek afspelen gaat met de sleutel van de link (PublicLink.request)", async () => {
    const link = { request: vi.fn().mockResolvedValue({ sid: "x" }) };
    const body: PlayIn = { profile };
    await media.publicPlay(link, "n/1", body);
    expect(link.request).toHaveBeenCalledWith("/api/v1/public/media/n%2F1/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  });

  it("de sessie sluiten en map.json gaan zonder header: de sid in het pad is de sleutel", async () => {
    const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/map.json")) {
        const segments = { grid_ms: 2000, origin_ms: 0, count: 3, last_frame_ms: 5960 };
        return new Response(JSON.stringify({ gen: 1, mime: "video/mp4", offset: -1, init: "init.mp4?g=1", segments }));
      }
      return new Response(null, { status: 204 });
    });
    await media.closeSession("film.mkv.abc", fetchFn as typeof fetch);
    const map = await media.getMap("film.mkv.abc", fetchFn as typeof fetch);
    expect(map.segments).toEqual({ grid_ms: 2000, origin_ms: 0, count: 3, last_frame_ms: 5960 });
    expect(fetchFn.mock.calls).toEqual([
      ["/api/v1/stream/film.mkv.abc", { method: "DELETE", credentials: "omit", keepalive: true }],
      ["/api/v1/stream/film.mkv.abc/map.json", { credentials: "omit", cache: "no-store" }],
    ]);
  });

  it("een fout bij map.json gooit de Response (de speler leest de code)", async () => {
    const res = new Response(JSON.stringify({ code: "media_session_gone" }), { status: 410 });
    await expect(media.getMap("s", (async () => res) as typeof fetch)).rejects.toBe(res);
  });

  it("query laat lege velden weg", () => {
    expect(media.query({})).toBe("");
    expect(media.query({ a: undefined, b: null, c: "", d: 0, e: false })).toBe("?d=0&e=false");
  });
});

describe("het tabblad Media op /beheer", () => {
  it("is er zodra GET /admin/media antwoordt; de stub (503 media_off) of een niet-beheerder niet", async () => {
    vi.spyOn(api, "loggedIn", "get").mockReturnValue(true);
    const request = vi.spyOn(api, "request").mockRejectedValueOnce(new ApiError(503, "uit", "media_off"));
    expect(media.adminMediaStatus).toBeInstanceOf(StatusStore);
    expect(await media.adminMediaStatus.load()).toBeNull();
    expect(request).toHaveBeenCalledWith("GET", "/api/v1/admin/media");
  });
});

describe("de segmenten van map.json (mediatijd, spike punten 10 en 12)", () => {
  it("het rooster: zoals SegmentGrid in app/media/types.py", () => {
    const grid = { grid_ms: 2000, origin_ms: 0, count: 3, last_frame_ms: 5960 };
    expect(media.segmentCount(grid)).toBe(3);
    expect([0, 1, 2].map((k) => media.segmentStart(grid, k))).toEqual([0, 2000, 4000]);
    expect(media.segmentOf(grid, 0)).toBe(0);
    expect(media.segmentOf(grid, 1999)).toBe(0);
    expect(media.segmentOf(grid, 2000)).toBe(1);
    // Een fragment voorbij count hoort bij het laatste segment; voor het begin bij het eerste
    expect(media.segmentOf(grid, 6100)).toBe(2);
    expect(media.segmentOf(grid, -50)).toBe(0);
  });

  it("met een starttijd S = 0,4 s: origin_ms = −400, het eerste segment is korter", () => {
    const grid = { grid_ms: 2000, origin_ms: -400, count: 4, last_frame_ms: 7000 };
    expect([0, 1, 2, 3].map((k) => media.segmentStart(grid, k))).toEqual([0, 1600, 3600, 5600]);
    expect(media.segmentOf(grid, 1599)).toBe(0);
    expect(media.segmentOf(grid, 1600)).toBe(1);
    expect(media.segmentOf(grid, 5600)).toBe(3);
  });

  it("remux: de lijst [begin_ms, duur_ms] uit de keyframe-index", () => {
    const list: [number, number][] = [
      [0, 6006],
      [6006, 6006],
      [12012, 8008],
    ];
    expect(media.segmentCount(list)).toBe(3);
    expect(media.segmentStart(list, 2)).toBe(12012);
    expect(media.segmentStart(list, 9)).toBe(12012);
    expect(media.segmentOf(list, 0)).toBe(0);
    expect(media.segmentOf(list, 6005)).toBe(0);
    expect(media.segmentOf(list, 6006)).toBe(1);
    expect(media.segmentOf(list, 99_999)).toBe(2);
    expect(media.segmentOf([], 10)).toBe(0);
    expect(media.segmentStart([], 0)).toBe(0);
  });
});

describe("de codes van §3.4", () => {
  it("25 codes, elk met zijn status; media_off is 503 (ook de stub)", () => {
    expect(MEDIA_ERROR_CODES).toHaveLength(25);
    expect(new Set(MEDIA_ERROR_CODES).size).toBe(25);
    for (const code of MEDIA_ERROR_CODES) expect(MEDIA_ERROR_STATUS[code]).toBeGreaterThanOrEqual(400);
    expect(MEDIA_ERROR_STATUS.media_off).toBe(503);
    expect(MEDIA_ERROR_STATUS.media_session_gone).toBe(410);
  });

  it.skipIf(!catalogs["../i18n/errors/media.ts"])("i18n/errors/media.ts kent precies deze codes (en de meldingen)", () => {
    const keys = Object.keys(catalogs["../i18n/errors/media.ts"]);
    for (const code of MEDIA_ERROR_CODES) expect(keys).toContain(code);
    const known = new Set<string>([...MEDIA_ERROR_CODES, ...MEDIA_NOTICES]);
    expect(keys.filter((k) => !known.has(k))).toEqual([]);
  });
});
