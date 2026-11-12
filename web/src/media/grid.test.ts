import { describe, expect, it } from "vitest";
import {
  CARD_GAP,
  CARD_TEXT,
  cardHref,
  cardKey,
  episodeCode,
  gridRowHeight,
  LOAD_AHEAD_ROWS,
  mergePage,
  nextEpisode,
  progressFraction,
  resumeAt,
  resumeHref,
  wantsMore,
} from "./grid";
import type { Card } from "./types";

function card(over: Partial<Card> = {}): Card {
  return {
    node_id: "n1",
    library_id: "l1",
    title: "Film",
    year: 2001,
    kind: "movie",
    poster: null,
    progress: null,
    watched: false,
    ...over,
  };
}

describe("cardKey en cardHref", () => {
  it("een item gaat naar zijn pagina, een serie naar de serie", () => {
    expect(cardHref(card())).toBe("/media/item/n1");
    expect(cardHref(card({ kind: "show", node_id: "map", show_id: "s 1" }))).toBe("/media/serie/s%201");
    expect(cardKey(card())).toBe("n:n1");
    expect(cardKey(card({ kind: "show", node_id: "map", show_id: "s1" }))).toBe("s:s1");
  });

  it("zonder bestand of serie: geen link", () => {
    expect(cardHref(card({ node_id: null }))).toBeNull();
    expect(cardHref(card({ kind: "show", node_id: "map", show_id: null }))).toBeNull();
  });

  it("een serie en het item met dezelfde node botsen niet", () => {
    expect(cardKey(card({ kind: "show", show_id: "x", node_id: "x" }))).not.toBe(cardKey(card({ node_id: "x" })));
  });
});

describe("gridRowHeight", () => {
  it("poster 2:3 plus tekst en ruimte", () => {
    expect(gridRowHeight(100)).toBe(150 + CARD_TEXT + CARD_GAP);
    expect(gridRowHeight(-5)).toBe(CARD_TEXT + CARD_GAP);
  });
});

describe("mergePage", () => {
  it("plakt de volgende pagina erachter, zonder dubbele", () => {
    const a = [card({ node_id: "a" }), card({ node_id: "b" })];
    const merged = mergePage(a, { items: [card({ node_id: "b" }), card({ node_id: "c" })], next: null });
    expect(merged.map((c) => c.node_id)).toEqual(["a", "b", "c"]);
    expect(a).toHaveLength(2); // het origineel blijft
  });
});

describe("wantsMore", () => {
  it("alleen met een cursor, niet bezig, en dicht bij het eind", () => {
    expect(wantsMore(60, 2, 5, "c", false)).toBe(false); // (2+3)*5 = 25 < 60
    expect(wantsMore(60, 12 - LOAD_AHEAD_ROWS, 5, "c", false)).toBe(true); // 60 >= 60
    expect(wantsMore(60, 20, 5, null, false)).toBe(false);
    expect(wantsMore(60, 20, 5, "c", true)).toBe(false);
  });

  it("nul kolommen telt als één", () => {
    expect(wantsMore(3, 0, 0, "c", false)).toBe(true);
  });
});

describe("progressFraction", () => {
  it("van 0 tot 1, ook bij rare waarden", () => {
    expect(progressFraction(null)).toBe(0);
    expect(progressFraction({ position_ms: 30, duration_ms: 120 })).toBe(0.25);
    expect(progressFraction({ position_ms: 500, duration_ms: 120 })).toBe(1);
    expect(progressFraction({ position_ms: 10, duration_ms: 0 })).toBe(0);
    expect(progressFraction({ position_ms: Number.NaN, duration_ms: 100 })).toBe(0);
  });
});

describe("resumeAt en resumeHref", () => {
  it("Verder op: hele seconden", () => {
    const c = card({ progress: { position_ms: 754_900, duration_ms: 6_000_000 } });
    expect(resumeAt(c)).toBe(754);
    expect(resumeHref(c)).toBe("/kijken/n1?t=754");
  });

  it("niet begonnen, gezien, vooraan of aan het eind: niets", () => {
    expect(resumeAt(card())).toBeNull();
    expect(resumeAt(card({ watched: true, progress: { position_ms: 60_000, duration_ms: 90_000 } }))).toBeNull();
    expect(resumeAt(card({ progress: { position_ms: 500, duration_ms: 90_000 } }))).toBeNull();
    expect(resumeAt(card({ progress: { position_ms: 90_000, duration_ms: 90_000 } }))).toBeNull();
    expect(resumeHref(card({ node_id: null, progress: { position_ms: 60_000, duration_ms: 90_000 } }))).toBeNull();
  });

  it("zonder duur mag het wel", () => {
    expect(resumeAt(card({ progress: { position_ms: 61_000, duration_ms: 0 } }))).toBe(61);
  });
});

describe("episodeCode", () => {
  it("S01E02, E12 bij absolute nummering, leeg zonder aflevering", () => {
    expect(episodeCode(1, 2)).toBe("S01E02");
    expect(episodeCode(12, 105)).toBe("S12E105");
    expect(episodeCode(null, 12)).toBe("E12");
    expect(episodeCode(undefined, 3)).toBe("E03");
    expect(episodeCode(1, null)).toBe("");
    expect(episodeCode(0, 1)).toBe("S00E01");
  });
});

describe("nextEpisode", () => {
  const ep = (n: number, over: Partial<Card> = {}) =>
    card({ node_id: `e${n}`, kind: "episode", season: 1, episode: n, ...over });

  it("eerst wat je half zag, dan wat je nog niet zag, anders de eerste", () => {
    const half = { position_ms: 60_000, duration_ms: 1_200_000 };
    expect(
      nextEpisode([{ season: 1, episodes: [ep(1, { watched: true }), ep(2), ep(3, { progress: half })] }]),
    ).toEqual({ card: ep(3, { progress: half }), resume: true });
    expect(nextEpisode([{ season: 1, episodes: [ep(1, { watched: true }), ep(2)] }])?.card.node_id).toBe("e2");
    expect(nextEpisode([{ season: 1, episodes: [ep(1, { watched: true })] }])).toEqual({
      card: ep(1, { watched: true }),
      resume: false,
    });
  });

  it("over seizoenen heen, en zonder bestand overgeslagen", () => {
    const seasons = [
      { season: 1, episodes: [ep(1, { watched: true }), ep(2, { node_id: null })] },
      { season: 2, episodes: [ep(1, { node_id: "s2e1", season: 2 })] },
    ];
    expect(nextEpisode(seasons)?.card.node_id).toBe("s2e1");
    expect(nextEpisode([])).toBeNull();
    expect(nextEpisode([{ season: null, episodes: [ep(1, { node_id: null })] }])).toBeNull();
  });
});
