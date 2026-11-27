import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import type { Card, Item, ShowDetail } from "../types";
import { episodeOf } from "./Actions";
import {
  candidateName,
  canMatch,
  canUnmatch,
  editFields,
  formOf,
  fromTmdb,
  isEmptyPatch,
  originalTitle,
  parseYear,
  patchOf,
  REVIEW_LIMIT,
  reviewList,
  setLock,
  setValue,
  TMDB_URL,
} from "./match";
import { hasReview, reviewText } from "./ReviewList";

afterEach(() => {
  vi.restoreAllMocks();
});

function item(over: Partial<Item> = {}): Item {
  return {
    node_id: "n1",
    library_id: "l1",
    title: "De Nephaven",
    year: 2004,
    kind: "movie",
    poster: null,
    progress: null,
    watched: false,
    overview: "Een haven.",
    backdrop: null,
    duration_ms: null,
    streams: [],
    subtitles: [],
    chapters: [],
    match_state: "matched",
    can_edit: true,
    parts: [],
    analysis: {},
    locked: [],
    sort_title: "nephaven",
    ...over,
  } as Item;
}

describe("de lijst Nakijken", () => {
  it("vraagt de route van M8 met cursor en limiet", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({ items: [], next: null });
    await reviewList("l/1");
    await reviewList("l1", "abc");
    expect(request.mock.calls).toEqual([
      ["GET", `/api/v1/media/libraries/l%2F1/review?limit=${REVIEW_LIMIT}`],
      ["GET", `/api/v1/media/libraries/l1/review?cursor=abc&limit=${REVIEW_LIMIT}`],
    ]);
  });

  it("alleen films en series met online opzoeken", () => {
    expect(hasReview({ kind: "movies", online: true })).toBe(true);
    expect(hasReview({ kind: "shows", online: true })).toBe(true);
    expect(hasReview({ kind: "movies", online: false })).toBe(false);
    expect(hasReview({ kind: "music", online: true })).toBe(false);
    expect(hasReview({ kind: "videos", online: true })).toBe(false);
  });

  it("een regel: titel met jaar, of met de code van de aflevering", () => {
    const base = { node_id: "n", title: "Storm", year: 1998, poster: null, progress: null, watched: false };
    expect(reviewText({ ...base, kind: "movie" } as Card)).toBe("Storm (1998)");
    expect(reviewText({ ...base, year: null, kind: "movie" } as Card)).toBe("Storm");
    expect(reviewText({ ...base, kind: "episode", season: 1, episode: 9 } as Card)).toBe("Storm · S01E09");
    expect(reviewText({ ...base, kind: "show" } as Card)).toBe("Storm (1998)");
  });
});

describe("wie wat mag", () => {
  it("koppelen: een film met schrijfrecht; een aflevering via haar serie", () => {
    expect(canMatch(item())).toBe(true);
    expect(canMatch(item({ can_edit: false }))).toBe(false);
    expect(canMatch(item({ kind: "episode" }))).toBe(false);
    expect(canMatch(item({ kind: "video" }))).toBe(false);
  });

  it("gegevens van TMDB en ontkoppelen", () => {
    expect(fromTmdb(item())).toBe(true);
    expect(fromTmdb(item({ match_state: "manual" }))).toBe(true);
    expect(fromTmdb(item({ match_state: "local" }))).toBe(false);
    expect(fromTmdb(item({ match_state: "review" }))).toBe(false);
    expect(fromTmdb(item({ kind: "video" }))).toBe(false);
    expect(canUnmatch(item())).toBe(true);
    expect(canUnmatch(item({ can_edit: false }))).toBe(false);
    expect(TMDB_URL).toMatch(/^https:\/\/www\.themoviedb\.org\/$/);
  });

  it("een serie koppelen via haar eerste aflevering met een bestand", () => {
    const card = (node_id: string | null) => ({ node_id, title: "x", kind: "episode" }) as Card;
    const detail = {
      show: { id: "s1" },
      seasons: [
        { season: 1, episodes: [card(null), card("e2")] },
        { season: 2, episodes: [card("e3")] },
      ],
    } as unknown as ShowDetail;
    expect(episodeOf(detail)).toBe("e2");
    expect(episodeOf({ ...detail, seasons: [] })).toBeNull();
  });
});

describe("kandidaten", () => {
  it("naam, oorspronkelijke titel en het jaar van het zoekveld", () => {
    expect(candidateName({ title: "Storm", year: 1998 })).toBe("Storm (1998)");
    expect(candidateName({ title: "Storm", year: null })).toBe("Storm");
    expect(originalTitle({ title: "De Nephaven", original_title: "The Fake Harbour" })).toBe("The Fake Harbour");
    expect(originalTitle({ title: "Storm", original_title: "storm " })).toBeNull();
    expect(originalTitle({ title: "Storm", original_title: null })).toBeNull();
    expect(parseYear("")).toBeNull();
    expect(parseYear(" 2004 ")).toBe(2004);
    for (const bad of ["20", "abcd", "1799", "2201", "2004a"]) expect(parseYear(bad)).toBe("bad");
  });
});

describe("bewerken", () => {
  it("de velden per soort", () => {
    expect(editFields("movie")).toEqual(["title", "sort_title", "year", "overview"]);
    expect(editFields("episode")).toEqual(["title", "sort_title", "season", "episode", "year", "overview"]);
  });

  it("het formulier van een item, alleen bekende velden vast", () => {
    const f = formOf(item({ locked: ["title", "match_state", "ids"], year: null, overview: null }));
    expect(f.values).toMatchObject({ title: "De Nephaven", sort_title: "nephaven", year: "", overview: "" });
    expect([...f.locked]).toEqual(["title"]);
  });

  it("niets gewijzigd: een lege patch", () => {
    const start = formOf(item());
    const out = patchOf("movie", start, start);
    expect(out.problem).toBeNull();
    expect(out.patch && isEmptyPatch(out.patch)).toBe(true);
  });

  it("een wijziging zet het vinkje aan; terug naar het origineel haalt het weg", () => {
    const start = formOf(item());
    let f = setValue(start, start, "title", "Mijn Haven");
    expect(f.locked.has("title")).toBe(true);
    expect(patchOf("movie", start, f)).toEqual({ patch: { title: "Mijn Haven" }, problem: null });
    f = setValue(f, start, "title", "De Nephaven");
    expect(f.locked.has("title")).toBe(false);
    expect(isEmptyPatch(patchOf("movie", start, f).patch ?? { title: "x" })).toBe(true);
  });

  it("het vinkje uit bij een gewijzigd veld zet de wijziging terug", () => {
    const start = formOf(item());
    let f = setValue(start, start, "year", "2003");
    f = setLock(f, start, "year", false);
    expect(f.values.year).toBe("2004");
    expect(isEmptyPatch(patchOf("movie", start, f).patch ?? { title: "x" })).toBe(true);
  });

  it("vastzetten zonder wijziging stuurt de waarde; losmaken stuurt unlock", () => {
    const start = formOf(item({ locked: ["overview"] }));
    let f = setLock(start, start, "year", true);
    f = setLock(f, start, "overview", false);
    expect(patchOf("movie", start, f)).toEqual({ patch: { year: 2004, unlock: ["overview"] }, problem: null });
  });

  it("lege velden en getallen", () => {
    const start = formOf(item({ kind: "episode", season: 1, episode: 2 }));
    let f = setValue(start, start, "overview", "");
    f = setValue(f, start, "year", "");
    f = setValue(f, start, "season", "3");
    f = setValue(f, start, "sort_title", "  ");
    expect(patchOf("episode", start, f)).toEqual({
      patch: { sort_title: "", season: 3, year: null, overview: null },
      problem: null,
    });
  });

  it("ongeldige invoer", () => {
    const start = formOf(item({ kind: "episode", season: 1, episode: 2 }));
    expect(patchOf("episode", start, setValue(start, start, "title", "  ")).problem).toBe("title");
    expect(patchOf("episode", start, setValue(start, start, "year", "1700")).problem).toBe("year");
    expect(patchOf("episode", start, setValue(start, start, "season", "-1")).problem).toBe("season");
    expect(patchOf("episode", start, setValue(start, start, "episode", "100000")).problem).toBe("episode");
    // Een film heeft geen seizoen: dat veld telt niet mee
    const movie = formOf(item());
    expect(patchOf("movie", movie, setValue(movie, movie, "season", "4")).patch).toEqual({});
  });
});
