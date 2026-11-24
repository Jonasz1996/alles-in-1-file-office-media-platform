// Hernoemen naar een andere extensie (0.8.3): gewoon, de vraag "Omzetten?" of de waarschuwing
// "Toch hernoemen", en wat elk antwoord doet (renameCheck.ts; de dialoog van useBrowserUi.tsx).

import { beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError, type FileNode } from "../api";
import { loadAreas, resetI18nForTests, t } from "../i18n";
import { type Ask, canConvert, extensionOf, type RenameDeps, renamePlan, renameWithCheck } from "./renameCheck";
import { dirNode, fileNode } from "./testNodes";

beforeAll(async () => {
  resetI18nForTests("nl");
  await loadAreas(["common", "browser"]);
});

describe("de extensie", () => {
  it("in kleine letters; zonder punt, met alleen een punt vooraan of achteraan: geen", () => {
    expect(extensionOf("Verslag.DOCX")).toBe("docx");
    expect(extensionOf("a.b.txt")).toBe("txt");
    expect(extensionOf("LEESMIJ")).toBe("");
    expect(extensionOf(".bashrc")).toBe("");
    expect(extensionOf("raar.")).toBe("");
  });
});

describe("wat hernoemen moet doen", () => {
  it.each([
    ["Verslag.docx", "Rapport.docx"],
    ["Verslag.docx", "Verslag.DOCX"],
    ["notities.txt", "notities.md"],
    ["data.csv", "data.txt"],
    ["log.log", "log.json"],
    ["LEESMIJ", "LEESMIJ.txt"], // geen extensie ervoor
    ["Verslag.docx", "Verslag"], // geen extensie erna
  ])("gewoon: %s → %s", (from, to) => {
    expect(renamePlan(from, to, true)).toEqual({ kind: "plain" });
    expect(renamePlan(from, to, false)).toEqual({ kind: "plain" });
  });

  it.each([
    ["Verslag.docx", "Verslag.txt", "docx", "txt"],
    ["notities.txt", "notities.docx", "txt", "docx"],
    ["Oud.doc", "Oud.odt", "doc", "odt"],
    ["cijfers.csv", "cijfers.xlsx", "csv", "xlsx"],
    ["Lijst.xlsx", "Lijst.csv", "xlsx", "csv"],
    ["Dia.ppt", "Dia.pptx", "ppt", "pptx"],
    ["Verslag.docx", "Verslag.pdf", "docx", "pdf"],
  ])("omzetten (als de editor dat nu kan): %s → %s", (from, to, a, b) => {
    expect(renamePlan(from, to, true)).toEqual({ kind: "convert", from: a, to: b });
    // Kan de editor het nu niet (office uit of weg): de waarschuwing
    expect(renamePlan(from, to, false)).toEqual({ kind: "warn", from: a, to: b });
  });

  it.each([
    ["Verslag.docx", "Verslag.xlsx"], // een andere groep
    ["foto.jpg", "foto.png"],
    ["foto.jpg", "foto.txt"],
    ["Verslag.pdf", "Verslag.docx"], // van PDF kan DS niet
    ["notities.txt", "notities.pptx"],
    ["archief.zip", "archief.docx"],
  ])("waarschuwen: %s → %s", (from, to) => {
    expect(renamePlan(from, to, true).kind).toBe("warn");
  });

  it("de groepen zijn die van de server (office_saveas.py)", () => {
    expect(canConvert("docx", "txt")).toBe(true);
    expect(canConvert("txt", "rtf")).toBe(true);
    expect(canConvert("xls", "ods")).toBe(true);
    expect(canConvert("odp", "pptx")).toBe(true);
    expect(canConvert("docx", "docx")).toBe(false);
    expect(canConvert("docx", "doc")).toBe(false); // DS maakt geen doc
    expect(canConvert("xlsx", "txt")).toBe(false);
  });
});

function deps(over: Partial<RenameDeps> = {}) {
  const asked: Ask[] = [];
  const d = {
    canConvert: vi.fn(async () => true),
    ask: vi.fn(async (a: Ask) => {
      asked.push(a);
      return true;
    }),
    rename: vi.fn(async (id: string, name: string) => fileNode({ id, name })),
    convertTo: vi.fn(async (_id: string, _format: string, name: string) => fileNode({ id: "kopie", name })),
    t: (key: string, vars?: Record<string, string>) => t(key as Parameters<typeof t>[0], vars),
    ...over,
  };
  return { d, asked };
}

const docx = fileNode({ id: "f1", name: "Verslag.docx" });

describe("de hernoem-dialoog", () => {
  it("gewoon hernoemen vraagt niets", async () => {
    const { d } = deps();
    const done = await renameWithCheck(docx, "Rapport.docx", d);
    expect(done).toEqual({ kind: "renamed", node: fileNode({ id: "f1", name: "Rapport.docx" }) });
    expect(d.ask).not.toHaveBeenCalled();
    expect(d.canConvert).not.toHaveBeenCalled();
  });

  it("een map: nooit een vraag, ook niet met een punt in de naam", async () => {
    const { d } = deps();
    await renameWithCheck(dirNode({ id: "m1", name: "Foto's.2025" }), "Foto's.docx", d);
    expect(d.ask).not.toHaveBeenCalled();
    expect(d.rename).toHaveBeenCalledWith("m1", "Foto's.docx");
  });

  it("docx → txt met de editor aan: Omzetten maakt een kopie met de nieuwe naam; het origineel blijft", async () => {
    const { d, asked } = deps();
    const done = await renameWithCheck(docx, "Verslag.txt", d);
    expect(asked).toEqual([
      {
        title: "Omzetten naar .txt?",
        message: expect.stringContaining("Omzetten maakt een kopie Verslag.txt"),
        confirm: "Omzetten",
      },
    ]);
    expect(d.convertTo).toHaveBeenCalledWith("f1", "txt", "Verslag.txt");
    expect(d.rename).not.toHaveBeenCalled();
    expect(done).toEqual({ kind: "converted", node: fileNode({ id: "kopie", name: "Verslag.txt" }) });
  });

  it("Omzetten? nee: niets (geen naam veranderd, geen kopie)", async () => {
    const { d } = deps({ ask: vi.fn(async () => false) });
    expect(await renameWithCheck(docx, "Verslag.txt", d)).toEqual({ kind: "none" });
    expect(d.rename).not.toHaveBeenCalled();
    expect(d.convertTo).not.toHaveBeenCalled();
  });

  it("zonder editor: de waarschuwing; Toch hernoemen verandert alleen de naam", async () => {
    const { d, asked } = deps({ canConvert: vi.fn(async () => false) });
    const done = await renameWithCheck(docx, "Verslag.txt", d);
    expect(asked).toEqual([
      {
        title: "Inhoud wordt niet omgezet",
        message: expect.stringContaining("de inhoud blijft docx"),
        confirm: "Toch hernoemen",
        danger: true,
      },
    ]);
    expect(d.rename).toHaveBeenCalledWith("f1", "Verslag.txt");
    expect(d.convertTo).not.toHaveBeenCalled();
    expect(done.kind).toBe("renamed");
  });

  it("de waarschuwing, en annuleren: niets", async () => {
    const { d } = deps({ ask: vi.fn(async () => false) });
    const jpg = fileNode({ id: "f2", name: "foto.jpg" });
    expect(await renameWithCheck(jpg, "foto.txt", d)).toEqual({ kind: "none" });
    expect(d.canConvert).not.toHaveBeenCalled(); // jpg kan de editor nooit omzetten
    expect(d.rename).not.toHaveBeenCalled();
  });

  it("een fout van de server gaat naar de aanroeper (die toont hem)", async () => {
    const { d } = deps({
      convertTo: vi.fn(async (): Promise<FileNode> => {
        throw new ApiError(502, "Dit bestand kon niet worden omgezet (code -3)", "office_convert_to_failed");
      }),
    });
    await expect(renameWithCheck(docx, "Verslag.odt", d)).rejects.toThrow("code -3");
  });

  it("in het Engels", async () => {
    resetI18nForTests("en");
    await loadAreas(["common", "browser"]);
    try {
      const { d, asked } = deps({ canConvert: vi.fn(async () => false) });
      await renameWithCheck(docx, "Verslag.txt", d);
      expect(asked[0]?.confirm).toBe("Rename anyway");
      const second = deps();
      await renameWithCheck(docx, "Verslag.txt", second.d);
      expect(second.asked[0]?.title).toBe("Convert to .txt?");
    } finally {
      resetI18nForTests("nl");
      await loadAreas(["common", "browser"]);
    }
  });
});
