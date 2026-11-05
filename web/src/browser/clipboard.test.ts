import { describe, expect, it } from "vitest";
import { ClipboardStore, ownerOf, planTransfer } from "./clipboard";
import { dropDecision, wantsCopy } from "./dragState";
import { dirNode, fileNode } from "./testNodes";

const anna = { id: "anna", display_name: "Anna" };

describe("klembord", () => {
  it("knippen, kopiëren, wissen en wat er niet meer is", () => {
    const clip = new ClipboardStore();
    let changes = 0;
    clip.subscribe(() => changes++);
    const a = fileNode({ id: "a" });
    const b = fileNode({ id: "b" });
    clip.set("cut", [a, b], "map");
    expect(clip.getSnapshot()).toMatchObject({ mode: "cut", from: "map" });
    expect(clip.isCut("a")).toBe(true);
    clip.drop(new Set(["a"]));
    expect(clip.getSnapshot()?.nodes.map((n) => n.id)).toEqual(["b"]);
    expect(clip.isCut("a")).toBe(false);
    clip.drop(new Set(["b"]));
    expect(clip.getSnapshot()).toBeNull();
    clip.set("copy", [a], null);
    expect(clip.isCut("a")).toBe(false);
    clip.clear();
    expect(clip.getSnapshot()).toBeNull();
    expect(changes).toBe(5);
    clip.set("copy", [], null);
    expect(clip.getSnapshot()).toBeNull();
  });

  it("eigenaar: owner, of jezelf zonder owner", () => {
    expect(ownerOf(fileNode({ id: "a" }), "ik")).toBe("ik");
    expect(ownerOf(fileNode({ id: "a", owner: anna, permission: "write" }), "ik")).toBe("anna");
    expect(ownerOf(fileNode({ id: "a", permission: "read" }), "ik")).toBeNull();
  });

  it("plakken: knippen verplaatst binnen een eigenaar, anders kopie; nooit in zichzelf", () => {
    const mine = fileNode({ id: "mine" });
    const hers = fileNode({ id: "hers", owner: anna, permission: "write" });
    const target = dirNode({ id: "doel" });
    expect(planTransfer([mine, hers, target], target, "cut", "ik")).toEqual({
      move: ["mine"],
      copy: ["hers"],
      skip: ["doel"],
    });
    expect(planTransfer([mine], target, "copy", "ik")).toEqual({ move: [], copy: ["mine"], skip: [] });
    const herFolder = dirNode({ id: "haar", owner: anna, permission: "write" });
    expect(planTransfer([mine, hers], herFolder, "cut", "ik")).toEqual({ move: ["hers"], copy: ["mine"], skip: [] });
  });
});

describe("loslaten", () => {
  const mine = fileNode({ id: "mine", parent_id: "hier" });
  const drag = { ids: ["mine"], nodes: [mine], from: "hier" };

  it("verplaatsen naar een eigen map, kopiëren met Ctrl/Alt", () => {
    expect(dropDecision(drag, dirNode({ id: "sub" }), false, "ik")).toEqual({ effect: "move" });
    expect(dropDecision(drag, dirNode({ id: "sub" }), true, "ik")).toEqual({ effect: "copy" });
    expect(wantsCopy({ ctrlKey: false, altKey: true, metaKey: false })).toBe(true);
    expect(wantsCopy({ ctrlKey: false, altKey: false, metaKey: false })).toBe(false);
  });

  it("een andere eigenaar: kopiëren", () => {
    const hers = dirNode({ id: "haar", owner: anna, permission: "write" });
    expect(dropDecision(drag, hers, false, "ik")).toEqual({ effect: "copy" });
  });

  it("weigeren met een reden", () => {
    expect(dropDecision(drag, fileNode({ id: "bestand" }), false, "ik")).toEqual({ effect: "none", reason: "notFolder" });
    expect(dropDecision({ ...drag, ids: ["sub"] }, dirNode({ id: "sub" }), false, "ik")).toEqual({
      effect: "none",
      reason: "itself",
    });
    expect(dropDecision(drag, dirNode({ id: "ro", owner: anna, permission: "read" }), false, "ik")).toEqual({
      effect: "none",
      reason: "readOnly",
    });
    expect(dropDecision(drag, dirNode({ id: "hier" }), false, "ik")).toEqual({ effect: "none", reason: "same" });
  });
});
