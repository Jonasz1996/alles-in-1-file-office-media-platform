import { describe, expect, it } from "vitest";
import { filterIds, filterWords, matches } from "./filter";
import { fileNode } from "./testNodes";

describe("filter in deze map", () => {
  it("woorden zonder hoofdletters en accenten", () => {
    expect(filterWords("  Café  Été ")).toEqual(["cafe", "ete"]);
    expect(filterWords("   ")).toEqual([]);
  });

  it("elk woord moet in de naam staan", () => {
    const n = fileNode({ id: "1", name: "Zomer in Évora 2026.jpg" });
    expect(matches(n, ["evora"])).toBe(true);
    expect(matches(n, ["zomer", "2026"])).toBe(true);
    expect(matches(n, ["zomer", "winter"])).toBe(false);
    expect(matches(n, [])).toBe(true);
  });

  it("filterIds houdt de volgorde en slaat onbekende ids over", () => {
    const nodes = new Map(
      [fileNode({ id: "a", name: "rapport.pdf" }), fileNode({ id: "b", name: "foto.jpg" }), fileNode({ id: "c", name: "Rapport 2.pdf" })].map(
        (n) => [n.id, n],
      ),
    );
    expect(filterIds(["c", "x", "b", "a"], nodes, "rapport")).toEqual(["c", "a"]);
    expect(filterIds(["c", "b"], nodes, "")).toEqual(["c", "b"]);
  });
});
