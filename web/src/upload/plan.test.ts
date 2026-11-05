import { describe, expect, it } from "vitest";
import { findConflicts, planConflicts } from "./plan";

const f = (name: string, parent = "p") => ({ file: new File(["x"], name), parent });

describe("planConflicts", () => {
  const taken = new Map([
    ["p", new Set(["a.txt", "foto.jpg"])],
    ["q", new Set(["a.txt"])],
  ]);
  const files = [f("A.TXT"), f("b.txt"), f("Foto.jpg"), f("a.txt", "q"), f("a.txt", "r")];

  it("vindt botsingen per map, zonder op hoofdletters te letten", () => {
    expect(findConflicts(files, taken)).toEqual([0, 2, 3]);
  });

  it("zonder botsing rename (nooit overschrijven), met botsing de keuze voor allemaal", () => {
    const replace = planConflicts(files, taken, "replace");
    expect(replace.create.map((c) => [c.file.name, c.parent, c.conflict])).toEqual([
      ["A.TXT", "p", "replace"],
      ["b.txt", "p", "rename"],
      ["Foto.jpg", "p", "replace"],
      ["a.txt", "q", "replace"],
      ["a.txt", "r", "rename"],
    ]);
    expect(planConflicts(files, taken, "keep").create.every((c) => c.conflict === "rename")).toBe(true);
    const skip = planConflicts(files, taken, "skip");
    expect(skip.create.map((c) => c.file.name)).toEqual(["b.txt", "a.txt"]);
    expect(skip.skipped.map((c) => `${c.parent}/${c.file.name}`)).toEqual(["p/A.TXT", "p/Foto.jpg", "q/a.txt"]);
  });

  it("per bestand een eigen keuze", () => {
    const out = planConflicts(files, taken, (_file, i) => (i === 0 ? "replace" : i === 2 ? "skip" : "keep"));
    expect(out.create.map((c) => `${c.file.name}:${c.conflict}`)).toEqual([
      "A.TXT:replace",
      "b.txt:rename",
      "a.txt:rename",
      "a.txt:rename",
    ]);
    expect(out.skipped.map((c) => c.file.name)).toEqual(["Foto.jpg"]);
  });

  it("houdt extra velden (pad) vast", () => {
    const out = planConflicts([{ ...f("x"), path: "M/x" }], new Map(), "skip");
    expect(out.create[0]).toMatchObject({ path: "M/x", conflict: "rename" });
  });
});
