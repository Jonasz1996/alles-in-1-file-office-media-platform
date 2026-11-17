// De rijen Verder kijken en Volgende aflevering (M7): waar Enter heen gaat, de teksten en het menu.
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests } from "../../i18n";
import type { Card } from "../types";
import { cardLabel, cardMenu, primaryHref, subText } from "./ContinueRow";

async function inLocale(locale: "nl" | "en") {
  resetI18nForTests(locale);
  await loadAreas(["media"]);
}

beforeAll(() => inLocale("nl"));
afterEach(() => inLocale("nl"));

function card(over: Partial<Card> = {}): Card {
  return {
    node_id: "n1",
    library_id: "l1",
    title: "Film",
    year: 2001,
    kind: "movie",
    poster: null,
    progress: { position_ms: 90_000, duration_ms: 600_000 },
    watched: false,
    ...over,
  };
}

const aflevering = (over: Partial<Card> = {}) =>
  card({ node_id: "e1", kind: "episode", title: "Pilot", season: 1, episode: 2, show_id: "s1", ...over });

describe("primaryHref", () => {
  it("verder kijken op de stand, anders afspelen; een serie naar de serie", () => {
    expect(primaryHref(card())).toBe("/kijken/n1?t=90");
    expect(primaryHref(card({ progress: null }))).toBe("/kijken/n1");
    expect(primaryHref(card({ kind: "show", node_id: "map", show_id: "s1" }))).toBe("/media/serie/s1");
  });
});

describe("teksten", () => {
  it("Verder op en de code van de aflevering, in het Nederlands en het Engels", async () => {
    expect(subText(card(), "continue")).toBe("Verder op 1:30");
    expect(subText(aflevering(), "continue")).toBe("S01E02 · Verder op 1:30");
    expect(subText(aflevering({ progress: null }), "next")).toBe("S01E02");
    expect(subText(card({ progress: null }), "next")).toBe("2001");
    expect(cardLabel(card(), "continue")).toBe("Film, verder op 1:30");
    expect(cardLabel(aflevering({ progress: null }), "next")).toBe("Pilot, S01E02");
    await inLocale("en");
    expect(subText(card(), "continue")).toBe("Resume at 1:30");
  });
});

describe("cardMenu", () => {
  it("verder kijken, naar het item en de serie, en gezien markeren", () => {
    const marked: Card[] = [];
    const items = cardMenu(aflevering(), "continue", (c) => marked.push(c));
    expect(items.map((i) => i.id)).toEqual(["resume", "item", "show", "watched"]);
    expect(items[0].label).toBe("Verder kijken vanaf 1:30");
    items[3].onSelect?.();
    expect(marked.map((c) => c.node_id)).toEqual(["e1"]);
  });

  it("Volgende aflevering: afspelen; zonder stand geen verder kijken", () => {
    expect(cardMenu(aflevering({ progress: null }), "next", () => undefined).map((i) => i.id)).toEqual([
      "play",
      "item",
      "show",
      "watched",
    ]);
    expect(cardMenu(aflevering(), "next", () => undefined).map((i) => i.id)).toEqual([
      "resume",
      "play",
      "item",
      "show",
      "watched",
    ]);
    expect(cardMenu(card({ kind: "show", show_id: "s1" }), "next", () => undefined)).toEqual([]);
  });
});
