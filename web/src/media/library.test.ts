import { describe, expect, it } from "vitest";
import {
  affectsHome,
  affectsItem,
  affectsLibrary,
  analysisRows,
  defaultName,
  extraKey,
  guessKind,
  kindIcon,
  NAME_MAX,
  nameToSend,
  parseSort,
  parseType,
  pickProblem,
  scanBusy,
  shownType,
  streamText,
  typesFor,
} from "./library";

describe("soorten en sorteren", () => {
  it("de soorten per bibliotheek; de eerste is de standaard van de server", () => {
    expect(typesFor("movies")).toEqual(["movie", "video"]);
    expect(typesFor("shows")).toEqual(["show", "video"]);
    expect(typesFor("videos")).toEqual(["video"]);
    expect(typesFor("music")).toEqual([]);
  });

  it("?sort= en ?type= uit het adres", () => {
    expect(parseSort(null)).toBe("title");
    expect(parseSort("added")).toBe("added");
    expect(parseSort("year")).toBe("year");
    expect(parseSort("rubbish")).toBe("title");
    expect(parseType("movies", "video")).toBe("video");
    // De standaard staat niet in het adres: undefined (de server kiest)
    expect(parseType("movies", "movie")).toBeUndefined();
    expect(parseType("movies", "show")).toBeUndefined();
    expect(parseType("music", "movie")).toBeUndefined();
    expect(shownType("shows", undefined)).toBe("show");
    expect(shownType("shows", "video")).toBe("video");
    expect(shownType("music", undefined)).toBeUndefined();
  });

  it("scannen bezig, iconen", () => {
    expect(scanBusy("queued")).toBe(true);
    expect(scanBusy("running")).toBe(true);
    expect(scanBusy("idle")).toBe(false);
    expect(scanBusy("failed")).toBe(false);
    expect(kindIcon("music")).toBe("audio");
    expect(kindIcon("shows")).toBe("video");
  });
});

describe("de dialoog", () => {
  it("de naam van de map, ingekort; leeg betekent de naam van de map", () => {
    expect(defaultName("  Films  ")).toBe("Films");
    expect(defaultName("x".repeat(300))).toHaveLength(NAME_MAX);
    expect(nameToSend("  ")).toBeUndefined();
    expect(nameToSend(" Mijn films ")).toBe("Mijn films");
    expect(nameToSend("y".repeat(250))).toHaveLength(NAME_MAX);
  });

  it("een voorstel voor het soort uit de naam", () => {
    expect(guessKind("Series")).toBe("shows");
    expect(guessKind("TV")).toBe("shows");
    expect(guessKind("Mijn shows")).toBe("shows");
    expect(guessKind("Muziek")).toBe("music");
    expect(guessKind("Music collection")).toBe("music");
    expect(guessKind("Video's")).toBe("videos");
    expect(guessKind("Home videos")).toBe("videos");
    expect(guessKind("Films")).toBe("movies");
    expect(guessKind("Seriesly")).toBe("movies");
    expect(guessKind("")).toBe("movies");
  });
});

describe("live gebeurtenissen", () => {
  const ev = (type: string, data: Record<string, unknown> = {}) => ({ type, data });

  it("de lijst van een bibliotheek", () => {
    expect(affectsLibrary(ev("media.item", { library_id: "l1", node_id: "n" }), "l1")).toBe(true);
    expect(affectsLibrary(ev("media.library", { library_id: "l1" }), "l1")).toBe(true);
    expect(affectsLibrary(ev("media.item", { library_id: "l2" }), "l1")).toBe(false);
    expect(affectsLibrary(ev("node.created", { library_id: "l1" }), "l1")).toBe(false);
    expect(affectsLibrary(ev("connected"), "l1")).toBe(true);
  });

  it("de start", () => {
    expect(affectsHome(ev("media.item"))).toBe(true);
    expect(affectsHome(ev("media.library"))).toBe(true);
    expect(affectsHome(ev("playback.progress"))).toBe(true);
    expect(affectsHome(ev("share.created"))).toBe(true);
    expect(affectsHome(ev("media.import"))).toBe(false);
    expect(affectsHome(ev("node.updated"))).toBe(false);
  });

  it("de pagina van een item", () => {
    expect(affectsItem(ev("media.item", { node_id: "n1" }), "n1")).toBe(true);
    expect(affectsItem(ev("playback.progress", { node_id: "n1" }), "n1")).toBe(true);
    expect(affectsItem(ev("playback.progress", { node_id: "n2" }), "n1")).toBe(false);
    expect(affectsItem(ev("media.library", { library_id: "n1" }), "n1")).toBe(false);
  });
});

describe("korte teksten", () => {
  it("de analyse in een vaste volgorde, alleen wat er is", () => {
    expect(analysisRows({ subs: "ready", probe: "ready", sprites: "pending" })).toEqual([
      ["probe", "ready"],
      ["subs", "ready"],
      ["sprites", "pending"],
    ]);
    expect(analysisRows({})).toEqual([]);
  });

  it("een spoor kort", () => {
    expect(streamText({ codec: "h264", lang: null, title: null })).toBe("h264");
    expect(streamText({ codec: "aac", lang: "nl", title: "Commentaar" })).toBe("aac · nl · Commentaar");
    expect(streamText({ codec: "ac3", lang: "", title: "x" })).toBe("ac3 · x");
  });

  it("de sleutel van een extra", () => {
    expect(extraKey("trailer")).toBe("trailer");
    expect(extraKey("other")).toBe("misc");
  });
});

describe("pickProblem", () => {
  const libs = [{ root_id: "a", name: "Films" }];
  it("een eigen map zonder bibliotheek mag", () => {
    expect(pickProblem({ id: "b", permission: "owner" }, libs)).toBeNull();
    expect(pickProblem({ id: "b" }, [])).toBeNull(); // zonder permission: van jou
  });
  it("een gedeelde map niet, ook niet met schrijven", () => {
    expect(pickProblem({ id: "b", permission: "write" }, libs)).toEqual({ reason: "notOwner" });
    expect(pickProblem({ id: "b", permission: "read" }, [])).toEqual({ reason: "notOwner" });
  });
  it("een map die al een bibliotheek is, noemt haar naam", () => {
    expect(pickProblem({ id: "a", permission: "owner" }, libs)).toEqual({ reason: "already", name: "Films" });
  });
});
