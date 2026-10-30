import { beforeAll, describe, expect, it } from "vitest";
import type { FileNode } from "../api";
import { loadAreas, resetI18nForTests } from "../i18n";
import { clickSelect, keySelect, SelectionStore } from "../ui/selection";
import type { TrashItem } from "./api";
import { actionIds, locationText, outcome, sortItems } from "./model";

beforeAll(async () => {
  resetI18nForTests("nl");
  await loadAreas(["trash"]);
});

function item(id: string, deleted: string, change: Partial<TrashItem> = {}): TrashItem {
  const node = { id, name: `naam-${id}`, kind: "file", deleted_at: deleted } as FileNode;
  return { node, location: [], size: 1, restore_to: "original", ...change };
}

describe("prullenbak", () => {
  it("Was in: het pad, de hoofdmap, of niets bij een album", () => {
    expect(locationText({ location: [] })).toBe("Mijn bestanden");
    expect(
      locationText({
        location: [
          { id: "a", name: "Foto's" },
          { id: "b", name: "2024" },
        ],
      }),
    ).toBe("Foto's / 2024");
    expect(locationText({ location: null })).toBeNull();
  });

  it("nieuwste eerst, bij gelijke tijd op naam", () => {
    const sorted = sortItems([
      item("b", "2026-01-01T00:00:00Z"),
      item("c", "2026-03-01T00:00:00Z"),
      item("a", "2026-01-01T00:00:00Z"),
    ]);
    expect(sorted.map((x) => x.node.id)).toEqual(["c", "a", "b"]);
  });

  it("de acties werken op de selectie in lijstvolgorde, anders op de focus", () => {
    const order = ["a", "b", "c", "d"];
    expect(actionIds(order, new Set(["d", "b"]), "a")).toEqual(["b", "d"]);
    expect(actionIds(order, new Set(), "c")).toEqual(["c"]);
    expect(actionIds(order, new Set(), "weg")).toEqual([]);
    expect(actionIds(order, new Set(), null)).toEqual([]);
  });

  it("selecteren met klik, Shift en de pijltjes, en wat weg is valt eruit", () => {
    const order = ["a", "b", "c", "d"];
    const store = new SelectionStore();
    store.set(clickSelect(store.getSnapshot(), order, "a", { shift: false, toggle: false }));
    store.set(clickSelect(store.getSnapshot(), order, "c", { shift: true, toggle: false }));
    expect([...store.getSnapshot().selected]).toEqual(["a", "b", "c"]);
    store.set(keySelect(store.getSnapshot(), order, "d", { shift: true, toggle: false }));
    expect(actionIds(order, store.getSnapshot().selected, store.getSnapshot().focus)).toEqual(["a", "b", "c", "d"]);
    // Na definitief verwijderen van b en c: de selectie houdt alleen wat er nog is
    store.retain(new Set(["a", "d"]));
    expect(actionIds(["a", "d"], store.getSnapshot().selected, store.getSnapshot().focus)).toEqual(["a", "d"]);
    store.selectAll(["a", "d"]);
    expect(store.getSnapshot().selected.size).toBe(2);
    store.clear();
    expect(actionIds(["a", "d"], store.getSnapshot().selected, store.getSnapshot().focus)).toEqual(["d"]);
  });

  it("de uitkomst van een bulkactie", () => {
    const r = outcome([
      { id: "a", ok: true },
      { id: "b", ok: false, code: "not_found", message: "Niet gevonden" },
      { id: "c", ok: true },
    ]);
    expect(r.ok).toBe(2);
    expect(r.failed.map((x) => x.id)).toEqual(["b"]);
  });
});
