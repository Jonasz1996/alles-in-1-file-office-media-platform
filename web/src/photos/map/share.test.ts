import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, type FileNode } from "../../api";
import { loadAreas, resetI18nForTests } from "../../i18n";
import LocationWarning, { LocationWarningText } from "../../shares/LocationWarning";
import { shareCheck } from "./api";
import {
  CHECKS_AT_ONCE,
  getShareCheck,
  mayHoldPlace,
  NOTHING,
  shareCheckPath,
  subjectOf,
  tallyChecks,
  warningLines,
} from "./share";
import type { ShareCheck } from "./types";

function node(kind: FileNode["kind"], mime: string | null = null, id = "n1"): FileNode {
  return {
    id,
    parent_id: kind === "album" ? null : "root",
    name: id,
    kind,
    mime,
    size: 1,
    etag: "e",
    created_at: "2026-11-03T10:00:00Z",
    modified_at: "2026-11-03T10:00:00Z",
    deleted_at: null,
    tags: [],
  };
}

async function useLocale(l: "nl" | "en"): Promise<void> {
  resetI18nForTests(l);
  await loadAreas(["photos_map"]);
}

afterEach(() => {
  vi.restoreAllMocks();
});

afterAll(() => resetI18nForTests("nl"));

describe("GET /photos/share-check: hetzelfde pad als ./api.ts (Z0, bevroren)", () => {
  it("met en zonder AbortSignal, alleen naar de app zelf", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({ with_location: 0, unknown: 0 });
    const ctl = new AbortController();
    await shareCheck("map/1");
    await getShareCheck("map/1", ctl.signal);
    expect(request.mock.calls).toEqual([
      ["GET", "/api/v1/photos/share-check?node=map%2F1"],
      ["GET", "/api/v1/photos/share-check?node=map%2F1", undefined, { signal: ctl.signal }],
    ]);
    expect(shareCheckPath("x")).toBe("/api/v1/photos/share-check?node=x");
  });
});

describe("waar de web naar vraagt", () => {
  it("een map en een album altijd; een bestand alleen als het een foto of video is", () => {
    expect(mayHoldPlace(node("dir"))).toBe(true);
    expect(mayHoldPlace(node("album"))).toBe(true);
    expect(mayHoldPlace(node("file", "image/jpeg"))).toBe(true);
    expect(mayHoldPlace(node("file", "IMAGE/HEIC"))).toBe(true);
    expect(mayHoldPlace(node("file", "video/mp4"))).toBe(true);
    expect(mayHoldPlace(node("file", "application/pdf"))).toBe(false);
    expect(mayHoldPlace(node("file", "audio/flac"))).toBe(false);
    expect(mayHoldPlace(node("file", null))).toBe(false);
  });

  it("één foto, één video, of meer (map, album, selectie)", () => {
    expect(subjectOf([node("file", "image/jpeg")])).toBe("photo");
    expect(subjectOf([node("file", "video/quicktime")])).toBe("video");
    expect(subjectOf([node("dir")])).toBe("many");
    expect(subjectOf([node("album")])).toBe("many");
    expect(subjectOf([node("file", "image/jpeg", "a"), node("file", "image/jpeg", "b")])).toBe("many");
    expect(subjectOf([])).toBe("many");
  });
});

describe("tallyChecks: alles opgeteld, hooguit vier tegelijk", () => {
  it("telt op, en een mislukte controle telt bij failed", async () => {
    const answers: Record<string, ShareCheck | Error> = {
      a: { with_location: 12, unknown: 0 },
      b: { with_location: 0, unknown: 3 },
      c: new Error("weg"),
      d: { with_location: 1, unknown: 1 },
    };
    const check = (id: string) => {
      const a = answers[id];
      return a instanceof Error ? Promise.reject(a) : Promise.resolve(a);
    };
    const tally = await tallyChecks(["a", "b", "c", "d"], check, new AbortController().signal);
    expect(tally).toEqual({ with_location: 13, unknown: 4, failed: 1 });
  });

  it("een telling die geen heel getal ≥ 0 is, telt als 0 (of naar beneden afgerond)", async () => {
    const bad = [
      { with_location: Number.NaN, unknown: -1 },
      { with_location: 2.7, unknown: Number.POSITIVE_INFINITY },
      { with_location: "5", unknown: null },
    ] as unknown as ShareCheck[];
    const tally = await tallyChecks(["0", "1", "2"], (id) => Promise.resolve(bad[Number(id)]), new AbortController().signal);
    expect(tally).toEqual({ with_location: 2, unknown: 0, failed: 0 });
  });

  it("nooit meer dan CHECKS_AT_ONCE verzoeken tegelijk, en elk id één keer", async () => {
    let open = 0;
    let most = 0;
    const asked: string[] = [];
    const check = async (id: string) => {
      asked.push(id);
      open++;
      most = Math.max(most, open);
      await new Promise((r) => setTimeout(r, 1));
      open--;
      return { with_location: 1, unknown: 0 };
    };
    const ids = Array.from({ length: 100 }, (_, i) => `n${i}`);
    const tally = await tallyChecks(ids, check, new AbortController().signal);
    expect(tally.with_location).toBe(100);
    expect(most).toBe(CHECKS_AT_ONCE);
    expect([...asked].sort()).toEqual([...ids].sort());
  });

  it("afgebroken (het dialoog ging dicht): geen telling, en er gaan geen nieuwe verzoeken uit", async () => {
    const ctl = new AbortController();
    const asked: string[] = [];
    const check = (id: string, signal: AbortSignal) => {
      asked.push(id);
      return new Promise<ShareCheck>((_, reject) => signal.addEventListener("abort", () => reject(signal.reason)));
    };
    const run = tallyChecks(["a", "b", "c", "d", "e", "f"], check, ctl.signal);
    ctl.abort();
    await expect(run).rejects.toThrow();
    expect(asked).toEqual(["a", "b", "c", "d"]);
  });
});

describe("de zinnen van de waarschuwing, in het Nederlands (Keuze 30)", () => {
  beforeEach(() => useLocale("nl"));

  it("bij een map, een album of een selectie: precies de zinnen van het ontwerp", () => {
    expect(warningLines({ with_location: 12, unknown: 0, failed: 0 }, "many")).toEqual([
      "12 foto's hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee.",
    ]);
    expect(warningLines({ with_location: 12, unknown: 3, failed: 0 }, "many")).toEqual([
      "12 foto's hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee.",
      "Van 3 video's weten we het niet (media staat uit).",
    ]);
    expect(warningLines({ with_location: 1, unknown: 1, failed: 0 }, "many")).toEqual([
      "1 foto hierin heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee.",
      "Van 1 video weten we het niet (media staat uit).",
    ]);
  });

  it("onbekend zonder foto met een plek: de zin zegt zelf waar het om gaat", () => {
    expect(warningLines({ with_location: 0, unknown: 3, failed: 0 }, "many")).toEqual([
      "Van 3 video's hierin weten we niet of ze een locatie hebben (media staat uit).",
    ]);
    expect(warningLines({ with_location: 0, unknown: 1, failed: 0 }, "many")).toEqual([
      "Van 1 video hierin weten we niet of hij een locatie heeft (media staat uit).",
    ]);
  });

  it("één bestand: deze foto, deze video, of onbekend", () => {
    expect(warningLines({ with_location: 1, unknown: 0, failed: 0 }, "photo")).toEqual([
      "Deze foto heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee.",
    ]);
    expect(warningLines({ with_location: 1, unknown: 0, failed: 0 }, "video")).toEqual([
      "Deze video heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee.",
    ]);
    expect(warningLines({ with_location: 0, unknown: 1, failed: 0 }, "video")).toEqual([
      "Of deze video een locatie heeft, weten we niet (media staat uit).",
    ]);
  });

  it("een controle die mislukte, zegt dat eerlijk; zonder plek niets", () => {
    expect(warningLines({ with_location: 2, unknown: 0, failed: 1 }, "many")).toEqual([
      "2 foto's hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee.",
      "Of hierin foto's met een locatie zitten, konden we niet nagaan.",
    ]);
    expect(warningLines({ with_location: 0, unknown: 0, failed: 1 }, "photo")).toEqual([
      "Of dit bestand een locatie heeft, konden we niet nagaan.",
    ]);
    expect(warningLines(NOTHING, "many")).toEqual([]);
    expect(warningLines(NOTHING, "photo")).toEqual([]);
  });

  it("staat in het dialoog als notitie; zonder zinnen niets, en het dialoog wacht er nooit op", () => {
    const lines = warningLines({ with_location: 12, unknown: 3, failed: 0 }, "many");
    expect(renderToStaticMarkup(createElement(LocationWarningText, { lines }))).toBe(
      '<div class="share-place-warning" role="note">' +
        "<p>12 foto&#x27;s hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee.</p>" +
        "<p>Van 3 video&#x27;s weten we het niet (media staat uit).</p></div>",
    );
    expect(renderToStaticMarkup(createElement(LocationWarningText, { lines: [] }))).toBe("");
    // De eerste weergave (vóór het antwoord) is leeg: het dialoog staat er al, zonder te wachten
    const request = vi.spyOn(api, "request");
    expect(renderToStaticMarkup(createElement(LocationWarning, { nodes: [node("dir")] }))).toBe("");
    expect(request).not.toHaveBeenCalled();
  });
});

describe("de zinnen van de waarschuwing, in het Engels", () => {
  beforeEach(() => useLocale("en"));

  it("meer, één, onbekend en mislukt", () => {
    expect(warningLines({ with_location: 12, unknown: 3, failed: 0 }, "many")).toEqual([
      "12 photos in here have a location in the file. Anyone who downloads them gets that location too.",
      "For 3 videos we don't know (media is off).",
    ]);
    expect(warningLines({ with_location: 0, unknown: 2, failed: 0 }, "many")).toEqual([
      "For 2 videos in here we don't know whether they have a location (media is off).",
    ]);
    expect(warningLines({ with_location: 1, unknown: 0, failed: 0 }, "photo")).toEqual([
      "This photo has a location in the file. Anyone who downloads it gets that location too.",
    ]);
    expect(warningLines({ with_location: 0, unknown: 1, failed: 0 }, "video")).toEqual([
      "We don't know whether this video has a location (media is off).",
    ]);
    expect(warningLines({ with_location: 0, unknown: 0, failed: 2 }, "many")).toEqual([
      "We could not check whether photos in here have a location.",
    ]);
  });
});
