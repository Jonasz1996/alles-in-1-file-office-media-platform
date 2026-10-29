import { describe, expect, it } from "vitest";
import type { Card, ShowDetail } from "../media/types";
import { cardTime, clock, episodeTag, episodes, nextEpisode, nextPart, prevEpisode } from "./next";

function ep(node: string, season: number | null, episode: number): Card {
  return { node_id: node, library_id: "l", title: node, year: null, kind: "episode", poster: null, progress: null, watched: false, season, episode };
}

const SHOW: ShowDetail = {
  show: { id: "s", library_id: "l", title: "Serie", year: null, overview: null, poster: null, backdrop: null, match_state: "local", can_edit: false },
  seasons: [
    { season: 2, episodes: [ep("b2", 2, 2), ep("b1", 2, 1)] },
    { season: 1, episodes: [ep("a1", 1, 1), ep("a2", 1, 2)] },
  ],
};

describe("de volgende aflevering", () => {
  it("seizoen na seizoen, aflevering na aflevering", () => {
    expect(episodes(SHOW).map((c) => c.node_id)).toEqual(["a1", "a2", "b1", "b2"]);
    expect(nextEpisode(SHOW, "a1")?.node_id).toBe("a2");
    expect(nextEpisode(SHOW, "a2")?.node_id).toBe("b1");
    expect(nextEpisode(SHOW, "b2")).toBeNull();
    expect(nextEpisode(SHOW, "onbekend")).toBeNull();
    expect(prevEpisode(SHOW, "b1")?.node_id).toBe("a2");
    expect(prevEpisode(SHOW, "a1")).toBeNull();
  });

  it("de volgorde van M2: specials (seizoen 0) achteraan, en geen kaart naar of van een special", () => {
    // Zoals items.show_detail de seizoenen geeft: 1, 2, en de specials achteraan
    const show: ShowDetail = {
      show: SHOW.show,
      seasons: [
        { season: 1, episodes: [ep("a1", 1, 1), ep("a2", 1, 2)] },
        { season: 2, episodes: [ep("b1", 2, 1)] },
        { season: 0, episodes: [ep("s1", 0, 1), ep("s2", 0, 2)] },
      ],
    };
    expect(episodes(show).map((c) => c.node_id)).toEqual(["a1", "a2", "b1", "s1", "s2"]);
    // Ook als de seizoenen in een andere volgorde komen
    const shuffled = { ...show, seasons: [show.seasons[2], show.seasons[1], show.seasons[0]] };
    expect(episodes(shuffled).map((c) => c.node_id)).toEqual(["a1", "a2", "b1", "s1", "s2"]);
    expect(nextEpisode(show, "a2")?.node_id).toBe("b1");
    expect(nextEpisode(show, "b1")).toBeNull(); // de laatste: geen special vanzelf
    expect(nextEpisode(show, "s1")?.node_id).toBe("s2");
    expect(prevEpisode(show, "s1")).toBeNull();
    expect(prevEpisode(show, "s2")?.node_id).toBe("s1");
    // Absolute nummering (geen seizoen) staat voor de seizoenen, zoals bij M2
    const absolute: ShowDetail = {
      show: SHOW.show,
      seasons: [
        { season: 1, episodes: [ep("a1", 1, 1)] },
        { season: null, episodes: [ep("x1", null, 1), ep("x2", null, 2)] },
      ],
    };
    expect(episodes(absolute).map((c) => c.node_id)).toEqual(["x1", "x2", "a1"]);
  });

  it("de kaart bij het begin van de aftiteling, anders 30 s voor het eind", () => {
    expect(cardTime(1500, 1_380_000)).toBe(1380);
    expect(cardTime(1500, null)).toBe(1470);
    expect(cardTime(1500, 0)).toBe(1470);
    // Een aftiteling voorbij het eind telt niet
    expect(cardTime(1500, 1_600_000)).toBe(1470);
    // Te kort voor een kaart zonder aftiteling
    expect(cardTime(50, null)).toBeNull();
    expect(cardTime(30, 24_000)).toBe(24);
    expect(cardTime(Number.NaN, null)).toBeNull();
  });

  it("een film in delen: het volgende deel", () => {
    const item = { parts: [{ node_id: "cd2", part: 2 }, { node_id: "cd1", part: 1 }] };
    expect(nextPart(item, "cd1")).toBe("cd2");
    expect(nextPart(item, "cd2")).toBeNull();
    expect(nextPart({ parts: [] }, "cd1")).toBeNull();
  });
});

describe("tijd", () => {
  it("m:ss en h:mm:ss", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(754)).toBe("12:34");
    expect(clock(6300)).toBe("1:45:00");
    expect(clock(-3)).toBe("0:00");
    expect(clock(Number.NaN)).toBe("0:00");
  });

  it("de kop van een aflevering", () => {
    expect(episodeTag({ season: 1, episode: 3 })).toBe("S1 · A3");
    expect(episodeTag({ season: null, episode: 12 })).toBe("A12");
    expect(episodeTag({ season: 1, episode: null })).toBeNull();
  });
});
