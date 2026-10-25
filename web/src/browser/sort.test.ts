import { describe, expect, it } from "vitest";
import { compareCodepoints, insertionIndex, isServerSort, nextSort, SERVER_SORT, serverCompare, sortIds } from "./sort";
import { dirNode, fileNode } from "./testNodes";

describe("servervolgorde", () => {
  it("mappen eerst, dan de naam zonder hoofdletters en accenten, dan het id", () => {
    const nodes = [
      fileNode({ id: "3", name: "beta.txt" }),
      fileNode({ id: "2", name: "Álfa.txt" }),
      dirNode({ id: "9", name: "zzz" }),
      fileNode({ id: "1", name: "alfa.txt" }),
    ];
    expect([...nodes].sort(serverCompare).map((n) => n.id)).toEqual(["9", "1", "2", "3"]);
  });

  it("vergelijkt per codepunt, zoals de database (niet per UTF-16-eenheid)", () => {
    // U+FF5E (één eenheid) komt vóór U+1F600 (een surrogaatpaar, D83D …) als codepunt
    expect(compareCodepoints("～", "\u{1f600}")).toBeLessThan(0);
    expect("～" < "\u{1f600}").toBe(false); // UTF-16 zegt het omgekeerde
    expect(compareCodepoints("a", "ab")).toBeLessThan(0);
    expect(compareCodepoints("b", "b")).toBe(0);
  });

  it("insertionIndex vindt de plek binnen wat geladen is", () => {
    const list = ["a", "c", "e"].map((name) => fileNode({ id: name, name }));
    const at = (name: string) => insertionIndex(list.length, (i) => list[i], fileNode({ id: name, name }));
    expect(at("0")).toBe(0);
    expect(at("b")).toBe(1);
    expect(at("d")).toBe(2);
    expect(at("z")).toBe(3);
    // Een plek die nog niet geladen is telt als "erna"
    expect(insertionIndex(5, (i) => list[i], fileNode({ id: "z", name: "z" }))).toBe(3);
  });
});

describe("eigen sortering", () => {
  const nodes = new Map(
    [
      fileNode({ id: "f10", name: "foto 10", size: 5, modified_at: "2026-10-03T00:00:00Z" }),
      fileNode({ id: "f2", name: "foto 2", size: 5, modified_at: "2026-10-01T00:00:00Z" }),
      dirNode({ id: "d", name: "zz map" }),
      fileNode({ id: "f1", name: "foto 1", size: 9, modified_at: "2026-10-02T00:00:00Z" }),
    ].map((n) => [n.id, n]),
  );
  const ids = ["d", "f1", "f10", "f2"];

  it("op naam zoals mensen dat verwachten: foto 2 vóór foto 10, mappen eerst", () => {
    expect(sortIds(ids, nodes, { key: "name", dir: "asc" })).toEqual(["d", "f1", "f2", "f10"]);
    expect(sortIds(ids, nodes, { key: "name", dir: "desc" })).toEqual(["d", "f10", "f2", "f1"]);
  });

  it("stabiel: bij gelijke grootte blijft de servervolgorde", () => {
    expect(sortIds(ids, nodes, { key: "size", dir: "asc" })).toEqual(["d", "f10", "f2", "f1"]);
    expect(sortIds(ids, nodes, { key: "size", dir: "desc" })).toEqual(["d", "f1", "f10", "f2"]);
  });

  it("op datum", () => {
    expect(sortIds(ids, nodes, { key: "modified", dir: "desc" })).toEqual(["d", "f10", "f1", "f2"]);
  });

  it("verandert de lijst zelf niet", () => {
    const before = [...ids];
    sortIds(ids, nodes, { key: "size", dir: "desc" });
    expect(ids).toEqual(before);
  });

  it("een klik op een kop: dezelfde draait om, een andere begint logisch", () => {
    expect(nextSort(SERVER_SORT, "name")).toEqual({ key: "name", dir: "desc" });
    expect(nextSort(SERVER_SORT, "size")).toEqual({ key: "size", dir: "desc" });
    expect(nextSort({ key: "size", dir: "desc" }, "size")).toEqual({ key: "size", dir: "asc" });
    expect(isServerSort(SERVER_SORT)).toBe(true);
    expect(isServerSort({ key: "name", dir: "desc" })).toBe(false);
  });
});
