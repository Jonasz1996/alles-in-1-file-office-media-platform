import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import {
  albumPath,
  applyLibraryEvent,
  artistPath,
  isDefinitiveOff,
  itemPath,
  LIBRARIES_PATH,
  libraryOn,
  libraryPath,
  MEDIA_HOME,
  type MediaOn,
  MUSIC_HOME,
  mediaEvents,
  mediaStatus,
  musicBar,
  playlistPath,
  STATUS_RETRY,
  STATUS_TTL,
  StatusStore,
  showMediaNav,
  showPath,
  streamUrl,
  wantsPlayer,
  watchPath,
} from "./index";
import type { Library } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
  mediaStatus.set(null);
  musicBar.hide();
});

function library(o: Partial<Library> = {}): Library {
  return {
    id: "lib1",
    name: "Films",
    kind: "movies",
    root_id: "map1",
    mine: true,
    item_count: 0,
    scan_state: "idle",
    scanned_at: null,
    online: false,
    ...o,
  };
}

const on: MediaOn = { libraries: [library()] };

/** Een store met een eigen klok en een nep-aanroep. */
function store(answers: (() => Promise<MediaOn>)[], canFetch = () => true) {
  let clock = 1_000_000;
  const fetchStatus = vi.fn(() => {
    const next = answers.shift();
    if (!next) throw new Error("geen antwoord meer");
    return next();
  });
  const s = new StatusStore<MediaOn>(fetchStatus, canFetch, () => clock);
  return {
    s,
    fetchStatus,
    tick(ms: number) {
      clock += ms;
    },
  };
}

describe("de status van media", () => {
  it("vraagt GET /api/v1/media/libraries: 200 is aan, met de bibliotheken", async () => {
    vi.spyOn(api, "loggedIn", "get").mockReturnValue(true);
    const request = vi.spyOn(api, "request").mockResolvedValue([library()]);
    mediaStatus.invalidate();
    expect(await mediaStatus.load()).toEqual(on);
    expect(request).toHaveBeenCalledWith("GET", LIBRARIES_PATH);
    expect(LIBRARIES_PATH).toBe("/api/v1/media/libraries");
  });

  it("503 media_off (ook de stub van M0) is uit: geen haken", async () => {
    vi.spyOn(api, "loggedIn", "get").mockReturnValue(true);
    vi.spyOn(api, "request").mockRejectedValue(new ApiError(503, "Media staat uit", "media_off"));
    mediaStatus.invalidate();
    expect(await mediaStatus.load()).toBeNull();
    expect(mediaStatus.peek()).toBeNull();
  });

  it("zonder login vraagt hij niets", async () => {
    vi.spyOn(api, "loggedIn", "get").mockReturnValue(false);
    const request = vi.spyOn(api, "request");
    mediaStatus.invalidate();
    expect(await mediaStatus.load()).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });

  it("media_off, 404 (een oudere server) en 403 zijn een antwoord; de rest is een storing", () => {
    expect(isDefinitiveOff(new ApiError(503, "uit", "media_off"))).toBe(true);
    expect(isDefinitiveOff(new ApiError(404, "Niet gevonden", "not_found"))).toBe(true);
    expect(isDefinitiveOff(new ApiError(403, "Nee", "guest"))).toBe(true);
    expect(isDefinitiveOff(new ApiError(503, "Bezig"))).toBe(false);
    expect(isDefinitiveOff(new ApiError(0, "geen verbinding", "network"))).toBe(false);
    expect(isDefinitiveOff(null)).toBe(false);
    expect(isDefinitiveOff("x")).toBe(false);
  });

  it("een antwoord geldt STATUS_TTL, ook 'uit'; een storing STATUS_RETRY", async () => {
    const off = () => Promise.reject(new ApiError(503, "uit", "media_off"));
    const { s, fetchStatus, tick } = store([off, () => Promise.resolve(on), () => Promise.reject(new TypeError("x"))]);
    expect(s.stale()).toBe(true);
    await s.load();
    expect(s.peek()).toBeNull();
    tick(STATUS_RETRY);
    expect(s.stale()).toBe(false); // uit is een antwoord: niet na 15 s opnieuw
    tick(STATUS_TTL - STATUS_RETRY);
    expect(s.stale()).toBe(true);
    await s.load();
    expect(s.peek()).toEqual(on);
    tick(STATUS_TTL);
    await s.load();
    expect(s.peek()).toBeNull();
    tick(STATUS_RETRY - 1);
    expect(s.stale()).toBe(false);
    tick(1);
    expect(s.stale()).toBe(true);
    expect(fetchStatus).toHaveBeenCalledTimes(3);
  });

  it("twee keer tegelijk vragen is één verzoek; get() vraagt alleen als hij oud is", async () => {
    const { s, fetchStatus } = store([() => Promise.resolve(on)]);
    const [a, b] = await Promise.all([s.load(), s.load()]);
    expect(a).toEqual(on);
    expect(b).toEqual(on);
    expect(s.get()).toEqual(on);
    expect(fetchStatus).toHaveBeenCalledTimes(1);
  });

  it("hetzelfde antwoord wekt niemand; een ander wel", async () => {
    const { s, tick } = store([() => Promise.resolve(on), () => Promise.resolve({ ...on }), () => Promise.resolve({ libraries: [] })]);
    const listener = vi.fn();
    const stop = s.subscribe(listener);
    await s.load();
    tick(STATUS_TTL);
    await s.load();
    expect(listener).toHaveBeenCalledTimes(1);
    tick(STATUS_TTL);
    await s.load();
    expect(listener).toHaveBeenCalledTimes(2);
    stop();
    s.set(null);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("invalidate maakt hem oud", async () => {
    const { s } = store([() => Promise.resolve(on)]);
    await s.load();
    expect(s.stale()).toBe(false);
    s.invalidate();
    expect(s.stale()).toBe(true);
  });
});

describe("media.library in de status", () => {
  it("een bekende bibliotheek: nieuwe telling en scanstaat, zonder verzoek", () => {
    const next = applyLibraryEvent(on, { library_id: "lib1", scan_state: "running", item_count: 12 });
    expect(next).toEqual({ libraries: [library({ scan_state: "running", item_count: 12 })] });
    // De oude status blijft zoals hij was
    expect(on.libraries[0].item_count).toBe(0);
  });

  it("een onbekende: opnieuw vragen; zonder status of zonder id: niets", () => {
    expect(applyLibraryEvent(on, { library_id: "nieuw", scan_state: "queued", item_count: 0 })).toBe("reload");
    expect(applyLibraryEvent(null, { library_id: "lib1" })).toBeNull();
    expect(applyLibraryEvent(on, {})).toBe(on);
  });

  it("mediaEvents zet de nieuwe status, of vraagt hem opnieuw; andere gebeurtenissen niet", async () => {
    vi.spyOn(api, "loggedIn", "get").mockReturnValue(true);
    const request = vi.spyOn(api, "request").mockResolvedValue([library(), library({ id: "lib2", root_id: "map2" })]);
    mediaStatus.set(on);
    mediaEvents({ type: "node.changed", data: { library_id: "lib1" } });
    mediaEvents({ type: "media.item", data: { library_id: "lib1", node_id: "x" } });
    expect(mediaStatus.peek()).toEqual(on);
    mediaEvents({ type: "media.library", data: { library_id: "lib1", scan_state: "running", item_count: 3 } });
    expect(mediaStatus.peek()?.libraries[0]).toMatchObject({ scan_state: "running", item_count: 3 });
    expect(request).not.toHaveBeenCalled();
    mediaEvents({ type: "media.library", data: { library_id: "lib2", scan_state: "queued", item_count: 0 } });
    await vi.waitFor(() => expect(mediaStatus.peek()?.libraries).toHaveLength(2));
    expect(request).toHaveBeenCalledWith("GET", LIBRARIES_PATH);
  });
});

describe("de regels van de haken", () => {
  it("Media en Muziek in het menu alleen als media aan staat; een gast alleen met een bibliotheek", () => {
    expect(showMediaNav(null, false)).toBe(false);
    expect(showMediaNav(null, true)).toBe(false);
    expect(showMediaNav({ libraries: [] }, false)).toBe(true);
    expect(showMediaNav({ libraries: [] }, true)).toBe(false);
    expect(showMediaNav(on, true)).toBe(true);
  });

  it("de bibliotheek op een map", () => {
    expect(libraryOn(on, "map1")?.id).toBe("lib1");
    expect(libraryOn(on, "map2")).toBeNull();
    expect(libraryOn(null, "map1")).toBeNull();
  });

  it("de speler alleen voor video, ingelogd, de huidige versie, en met media aan", () => {
    expect(wantsPlayer("video", "user", false, on)).toBe(true);
    expect(wantsPlayer("video", "user", false, null)).toBe(false);
    expect(wantsPlayer("audio", "user", false, on)).toBe(false);
    expect(wantsPlayer("video", "public", false, on)).toBe(false);
    expect(wantsPlayer("video", "user", true, on)).toBe(false);
  });
});

describe("de adressen (routes.tsx)", () => {
  it("elke pagina, met het id veilig in het pad", () => {
    expect(MEDIA_HOME).toBe("/media");
    expect(MUSIC_HOME).toBe("/muziek");
    expect(libraryPath("a/b")).toBe("/media/bibliotheek/a%2Fb");
    expect(itemPath("n1")).toBe("/media/item/n1");
    expect(showPath("s1")).toBe("/media/serie/s1");
    expect(watchPath("n1")).toBe("/kijken/n1");
    expect(watchPath("n1", 754.9)).toBe("/kijken/n1?t=754");
    expect(watchPath("n1", 0)).toBe("/kijken/n1");
    expect(albumPath("al1")).toBe("/muziek/album/al1");
    expect(artistPath("the beatles")).toBe("/muziek/artiest/the%20beatles");
    expect(playlistPath("p1")).toBe("/muziek/lijst/p1");
  });

  it("een adres onder /api/v1/stream/{sid}/", () => {
    expect(streamUrl("film.mkv.AbC-_1", "map.json")).toBe("/api/v1/stream/film.mkv.AbC-_1/map.json");
    expect(streamUrl("s", "/subs/e2.vtt")).toBe("/api/v1/stream/s/subs/e2.vtt");
    expect(streamUrl("a b", "3.m4s")).toBe("/api/v1/stream/a%20b/3.m4s");
  });
});

describe("de muziekbalk", () => {
  it("show en hide, en alleen een verandering wekt", () => {
    const listener = vi.fn();
    const stop = musicBar.subscribe(listener);
    expect(musicBar.visible()).toBe(false);
    musicBar.show();
    musicBar.show();
    expect(musicBar.visible()).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    musicBar.hide();
    expect(listener).toHaveBeenCalledTimes(2);
    stop();
  });
});
