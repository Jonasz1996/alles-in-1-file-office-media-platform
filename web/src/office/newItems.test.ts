import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, type FileNode } from "../api";
import { dirNode, fileNode } from "../browser/testNodes";
import { loadAreas, resetI18nForTests } from "../i18n";
import { announcer, toasts } from "../ui/toast";
import { newDocument } from "./api";
import { createDocument, NEW_KINDS, type NewItemsDeps, newItems } from "./newItems";
import type { OfficeStatus } from "./types";

const on: OfficeStatus = {
  enabled: true,
  editing: true,
  state: "ok",
  edit_formats: ["docx", "xlsx", "pptx"],
  new_kinds: ["docx", "xlsx", "pptx"],
  max_bytes: 104857600,
};

const folder = dirNode({ id: "map", parent_id: "root" });
const made = (name: string): FileNode => fileNode({ id: "nieuw", name, parent_id: "map", mime: null, size: 2024 });

function deps(create: NewItemsDeps["create"] = async () => made("Nieuw document.docx")) {
  return { create: vi.fn(create), open: vi.fn() };
}

function errors(): string[] {
  return toasts
    .getSnapshot()
    .filter((x) => x.kind === "error")
    .map((x) => x.message);
}

beforeAll(async () => {
  resetI18nForTests("nl");
  await loadAreas(["common", "browser"]);
});

beforeEach(() => {
  for (const item of toasts.getSnapshot()) toasts.dismiss(item.id);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("welke items er in het menu Nieuw komen", () => {
  it("zonder office, bij een oudere server of met office uit: geen, dus precies fase 3", () => {
    expect(newItems({ folder, status: null, added: () => {} })).toEqual([]);
    expect(
      newItems({ folder, status: { ...on, enabled: false, editing: false, state: "off" }, added: () => {} }),
    ).toEqual([]);
  });

  it("als de editor nu niet bereikbaar is of verkeerd staat: ook geen", () => {
    for (const state of ["down", "starting", "misconfigured"] as const) {
      expect(newItems({ folder, status: { ...on, editing: false, state }, added: () => {} })).toEqual([]);
    }
  });

  it("met bewerken aan: Document, Werkblad en Presentatie, in die volgorde", () => {
    const items = newItems({ folder, status: on, added: () => {} });
    expect(items.map((i) => i.id)).toEqual(["new-docx", "new-xlsx", "new-pptx"]);
    expect(items.map((i) => i.label)).toEqual(["Document", "Werkblad", "Presentatie"]);
    expect(items.every((i) => i.icon === "document" && !i.disabled)).toBe(true);
    expect(NEW_KINDS).toEqual(["docx", "xlsx", "pptx"]);
  });

  it("alleen de soorten die de server kan maken (new_kinds), in de vaste volgorde", () => {
    const items = newItems({ folder, status: { ...on, new_kinds: ["pptx", "docx"] }, added: () => {} });
    expect(items.map((i) => i.id)).toEqual(["new-docx", "new-pptx"]);
    expect(newItems({ folder, status: { ...on, new_kinds: [] }, added: () => {} })).toEqual([]);
  });

  it("in het Engels", async () => {
    resetI18nForTests("en");
    await loadAreas(["common", "browser"]);
    try {
      const items = newItems({ folder, status: on, added: () => {} });
      expect(items.map((i) => i.label)).toEqual(["Document", "Spreadsheet", "Presentation"]);
    } finally {
      resetI18nForTests("nl");
      await loadAreas(["common", "browser"]);
    }
  });
});

describe("een item kiezen", () => {
  it("maakt het document in deze map, zet het in de lijst, meldt het en opent de editor", async () => {
    const d = deps();
    const added = vi.fn();
    const [docx] = newItems({ folder, status: on, added }, d);
    docx?.onSelect?.();
    await vi.waitFor(() => expect(d.open).toHaveBeenCalledTimes(1));
    expect(d.create).toHaveBeenCalledWith({ parent_id: "map", kind: "docx" });
    expect(added).toHaveBeenCalledWith(made("Nieuw document.docx"));
    expect(d.open).toHaveBeenCalledWith(made("Nieuw document.docx"));
    expect(announcer.getSnapshot().polite.trim()).toBe("Nieuw document.docx gemaakt");
    expect(errors()).toEqual([]);
  });

  it("elk item geeft zijn eigen soort mee, zonder naam (die kiest de server in jouw taal)", async () => {
    for (const kind of NEW_KINDS) {
      const d = deps(async (body) => made(`Nieuw.${body.kind}`));
      const node = await createDocument({ folder, added: () => {} }, kind, d);
      expect(node?.name).toBe(`Nieuw.${kind}`);
      expect(d.create).toHaveBeenCalledWith({ parent_id: "map", kind });
      expect(Object.keys(d.create.mock.calls[0]?.[0] ?? {})).not.toContain("name");
    }
  });

  it("een fout wordt een melding; dan geen nieuw bestand in de lijst en geen editor", async () => {
    const d = deps(async () => {
      throw new ApiError(413, "Je opslag is vol", "no_room");
    });
    const added = vi.fn();
    const node = await createDocument({ folder, added }, "xlsx", d);
    expect(node).toBeNull();
    expect(added).not.toHaveBeenCalled();
    expect(d.open).not.toHaveBeenCalled();
    expect(errors()).toEqual(["Je opslag is vol"]);
  });

  it("twee keer snel kiezen in dezelfde map maakt er één; daarna kan het weer", async () => {
    let finish: (node: FileNode) => void = () => {};
    const d = deps(
      () =>
        new Promise<FileNode>((resolve) => {
          finish = resolve;
        }),
    );
    const first = createDocument({ folder, added: () => {} }, "docx", d);
    expect(await createDocument({ folder, added: () => {} }, "pptx", d)).toBeNull();
    expect(d.create).toHaveBeenCalledTimes(1);
    // Een andere map wacht niet op deze
    const other = dirNode({ id: "andere", parent_id: "root" });
    const quick = deps();
    expect(await createDocument({ folder: other, added: () => {} }, "docx", quick)).not.toBeNull();
    finish(made("Nieuw document.docx"));
    expect(await first).not.toBeNull();
    expect(await createDocument({ folder, added: () => {} }, "docx", deps())).not.toBeNull();
  });

  it("ook na een fout kan het weer", async () => {
    const failing = deps(async () => {
      throw new ApiError(404, "Deze map bestaat niet (meer)", "folder_not_found");
    });
    expect(await createDocument({ folder, added: () => {} }, "docx", failing)).toBeNull();
    expect(await createDocument({ folder, added: () => {} }, "docx", deps())).not.toBeNull();
  });

  it("de echte aanroep: POST /api/v1/office/new met de map en de soort", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue(made("Nieuwe presentatie.pptx"));
    const open = vi.fn();
    const node = await createDocument({ folder, added: () => {} }, "pptx", { create: newDocument, open });
    expect(request).toHaveBeenCalledWith("POST", "/api/v1/office/new", { parent_id: "map", kind: "pptx" });
    expect(node?.name).toBe("Nieuwe presentatie.pptx");
    expect(open).toHaveBeenCalledWith(node);
  });
});
