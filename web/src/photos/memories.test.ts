// Herinneringen per map verbergen (0.8.2, docs/fase-6/z6.md Open): "Een map verbergen…" naast de
// kop van het blok opent de mappenkiezer; alleen een eigen map, niet Mijn bestanden zelf. De API
// (POST /photos/memories/hide met kind folder) verbergt de map met alles eronder.

import { afterEach, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests, t } from "../i18n";
import { folderProblem } from "./Memories";
import memoriesSource from "./Memories.tsx?raw";

afterEach(() => {
  resetI18nForTests("nl");
});

describe("een map uit de herinneringen", () => {
  it("alleen een eigen map, en niet Mijn bestanden zelf", () => {
    expect(folderProblem({ parent_id: "p", permission: "owner" })).toBeNull();
    // Een oudere server stuurt geen permission: dan is het van jou
    expect(folderProblem({ parent_id: "p" })).toBeNull();
    expect(folderProblem({ parent_id: "p", permission: "write" })).toBe("photos_people.memories.folderNotOwn");
    expect(folderProblem({ parent_id: "p", permission: "read" })).toBe("photos_people.memories.folderNotOwn");
    expect(folderProblem({ parent_id: null, permission: "owner" })).toBe("photos_people.memories.folderRoot");
    // Een gedeelde hoofdmap van een ander: eerst dat hij niet van jou is
    expect(folderProblem({ parent_id: null, permission: "read" })).toBe("photos_people.memories.folderNotOwn");
  });

  it("de zinnen staan er in het Nederlands en het Engels", async () => {
    resetI18nForTests("nl");
    await loadAreas(["photos_people"]);
    expect(t("photos_people.memories.hideFolder")).toBe("Een map verbergen…");
    expect(t("photos_people.memories.folderHidden", { name: "Bonnetjes" })).toBe(
      "Foto's uit Bonnetjes komen niet meer in je herinneringen.",
    );
    resetI18nForTests("en");
    await loadAreas(["photos_people"]);
    expect(t("photos_people.memories.hideFolder")).toBe("Hide a folder…");
  });

  it("verbergt met kind folder, met Ongedaan maken, via de mappenkiezer (lazy, gebied browser)", () => {
    expect(memoriesSource).toContain('await hideMemories({ kind: "folder", ref: id });');
    expect(memoriesSource).toContain('unhideMemories("folder", id).then(load,');
    expect(memoriesSource).toContain('import("../FolderPicker").then((m) => ({ default: m.FolderPicker }))');
    expect(memoriesSource).toContain('["browser"],');
    expect(memoriesSource).toContain("const problem = folderProblem(f);");
    // De mappenkiezer zit niet statisch in het chunk van de herinneringen
    expect(memoriesSource).not.toMatch(/^import \{[^}]*FolderPicker[^}]*\} from "\.\.\/FolderPicker";$/m);
  });
});
