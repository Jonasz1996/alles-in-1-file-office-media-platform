import { describe, expect, it } from "vitest";
import { mapKey, panStep } from "./keys";

const SIZE = { w: 800, h: 600 };
const key = (k: string, mods: Partial<Record<"ctrlKey" | "metaKey" | "altKey" | "shiftKey", boolean>> = {}) => ({
  key: k,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  shiftKey: false,
  ...mods,
});

describe("de toetsen van de kaart (Keuze 31)", () => {
  it("de pijlen schuiven een kwart venster; pijl links kijkt verder naar het westen", () => {
    expect(mapKey(key("ArrowLeft"), SIZE)).toEqual({ kind: "pan", dx: 200, dy: 0 });
    expect(mapKey(key("ArrowRight"), SIZE)).toEqual({ kind: "pan", dx: -200, dy: 0 });
    expect(mapKey(key("ArrowUp"), SIZE)).toEqual({ kind: "pan", dx: 0, dy: 150 });
    expect(mapKey(key("ArrowDown"), SIZE)).toEqual({ kind: "pan", dx: 0, dy: -150 });
  });

  it("met Shift een half venster", () => {
    expect(panStep(SIZE, true)).toEqual({ x: 400, y: 300 });
    expect(mapKey(key("ArrowLeft", { shiftKey: true }), SIZE)).toEqual({ kind: "pan", dx: 400, dy: 0 });
  });

  it("+ en − zoomen (ook = en _ op dezelfde toets), Home zet al je foto's in beeld", () => {
    expect(mapKey(key("+"), SIZE)).toEqual({ kind: "zoom", by: 1 });
    expect(mapKey(key("+", { shiftKey: true }), SIZE)).toEqual({ kind: "zoom", by: 1 });
    expect(mapKey(key("="), SIZE)).toEqual({ kind: "zoom", by: 1 });
    expect(mapKey(key("-"), SIZE)).toEqual({ kind: "zoom", by: -1 });
    expect(mapKey(key("_", { shiftKey: true }), SIZE)).toEqual({ kind: "zoom", by: -1 });
    expect(mapKey(key("Home"), SIZE)).toEqual({ kind: "home" });
  });

  it("met Ctrl, Cmd of Alt is de toets niet van de kaart (Ctrl + is de zoom van de browser)", () => {
    for (const mod of ["ctrlKey", "metaKey", "altKey"] as const) {
      expect(mapKey(key("+", { [mod]: true }), SIZE)).toBeNull();
      expect(mapKey(key("ArrowLeft", { [mod]: true }), SIZE)).toBeNull();
      expect(mapKey(key("Home", { [mod]: true }), SIZE)).toBeNull();
    }
  });

  it("andere toetsen laat de kaart liggen (Tab, Enter, Escape, letters)", () => {
    for (const k of ["Tab", "Enter", " ", "Escape", "End", "PageUp", "a", "k"]) expect(mapKey(key(k), SIZE)).toBeNull();
  });
});
