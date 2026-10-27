import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { loadAreas, resetI18nForTests } from "../i18n";
import { officeStatus } from "../office/api";
import type { OfficeStatus } from "../office/types";
import {
  type ActionEnv,
  actionLabel,
  actionMenuItems,
  allowed,
  availability,
  type BrowserAction,
  browserActions,
  actionsVersion,
  findAction,
  GROUPS,
  MAX_SHARE,
  registerAction,
  subscribeActions,
} from "./actions";
import { dirNode, fileNode } from "./testNodes";

const anna = { id: "anna", display_name: "Anna" };
const env: ActionEnv = { folder: dirNode({ id: "map", parent_id: "root" }), clip: null, me: "ik", guest: false };

function action(id: string): BrowserAction {
  const a = findAction(id);
  if (!a) throw new Error(`geen actie ${id}`);
  return a;
}

beforeAll(async () => {
  resetI18nForTests("nl");
  await loadAreas(["common", "browser"]);
});

describe("rechten over de hele selectie", () => {
  const mine = fileNode({ id: "a" });
  const readOnly = fileNode({ id: "b", owner: anna, permission: "read" });
  const shareRoot = dirNode({ id: "c", owner: anna, permission: "write", parent_id: null });

  it("allowed is de doorsnede van nodeActions()", () => {
    expect(allowed([mine], "trash")).toBe(true);
    expect(allowed([mine, readOnly], "trash")).toBe(false);
    expect(allowed([mine, readOnly], "copy")).toBe(true);
    expect(allowed([], "copy")).toBe(false);
    expect(allowed([shareRoot], "rename")).toBe(false);
  });

  it("wat niet kan, staat uitgeschakeld met de reden", () => {
    expect(availability(action("trash"), [mine, readOnly], env)).toEqual({
      visible: true,
      enabled: false,
      reason: "Je mag hier alleen kijken",
    });
    expect(availability(action("rename"), [shareRoot], env).reason).toMatch(/alleen de eigenaar/);
    expect(availability(action("trash"), [mine], env)).toEqual({ visible: true, enabled: true });
  });

  it("één item: hernoemen, tags en versies; meer: verborgen", () => {
    const two = [mine, fileNode({ id: "d" })];
    expect(availability(action("rename"), two, env).visible).toBe(false);
    expect(availability(action("versions"), [mine], env).visible).toBe(true);
    expect(availability(action("versions"), [dirNode({ id: "x" })], env).visible).toBe(false);
    expect(availability(action("tags"), two, env).visible).toBe(false);
  });

  it("delen: niet voor gasten, hooguit MAX_SHARE items", () => {
    const many = Array.from({ length: MAX_SHARE + 1 }, (_, i) => fileNode({ id: `n${i}` }));
    expect(availability(action("share"), many, env)).toEqual({
      visible: true,
      enabled: false,
      reason: "Hooguit 100 items tegelijk; deel liever de map",
    });
    expect(availability(action("share"), many.slice(0, MAX_SHARE), env).enabled).toBe(true);
    expect(availability(action("share"), [mine], { ...env, guest: true }).visible).toBe(false);
    expect(availability(action("share"), [readOnly], env).visible).toBe(false);
  });

  it("album: alleen eigen foto's en video's", () => {
    const photo = fileNode({ id: "p", mime: "image/jpeg" });
    expect(availability(action("addToAlbum"), [photo], env).enabled).toBe(true);
    expect(availability(action("addToAlbum"), [photo, mine], env)).toMatchObject({ enabled: false });
    expect(availability(action("addToAlbum"), [fileNode({ id: "q", mime: "image/png", owner: anna, permission: "write" })], env).visible).toBe(false);
  });

  it("plakken alleen met iets op het klembord, en alleen waar je mag schrijven", () => {
    const paste = action("paste");
    expect(availability(paste, [], env).visible).toBe(false);
    const clip = { mode: "copy" as const, nodes: [mine], from: "elders" };
    expect(availability(paste, [], { ...env, clip }).enabled).toBe(true);
    const ro = dirNode({ id: "ro", owner: anna, permission: "read" });
    expect(availability(paste, [], { ...env, clip, folder: ro })).toMatchObject({ enabled: false });
  });

  it("zonder selectie niets, behalve wat zonder selectie werkt", () => {
    expect(availability(action("trash"), [], env).visible).toBe(false);
    expect(availability(action("download"), [], env).visible).toBe(false);
  });

  it("albums staan buiten de boom: niet kopiëren, downloaden of hernoemen", () => {
    const album = fileNode({ id: "al", kind: "album", mime: null });
    expect(availability(action("copy"), [album], env).visible).toBe(false);
    expect(availability(action("download"), [album], env).visible).toBe(false);
    expect(allowed([album], "rename")).toBe(false);
  });
});

describe("het register", () => {
  it("groepen in vaste volgorde, met de lege groep omzetten (fase 10)", () => {
    expect(GROUPS).toEqual(["open", "edit", "share", "info", "convert", "trash"]);
    const groups = browserActions().map((a) => GROUPS.indexOf(a.group));
    expect(groups).toEqual([...groups].sort((a, b) => a - b));
    // Omzetten heeft in fase 4 alleen "Downloaden als PDF" (de haak van office)
    expect(browserActions().filter((a) => a.group === "convert").map((a) => a.id)).toEqual(["pdf"]);
  });

  it("labels: per selectie en pas bij het tonen vertaald", () => {
    const one = fileNode({ id: "a" });
    expect(actionLabel(action("download"), [one])).toBe("Downloaden");
    expect(actionLabel(action("download"), [one, fileNode({ id: "b" })])).toBe("Downloaden als zip");
    expect(actionLabel(action("download"), [dirNode({ id: "d" })])).toBe("Downloaden als zip");
  });

  it("menu-items: scheidingslijn tussen groepen, uitgeschakeld met reden, run doet de actie", () => {
    const ran: string[] = [];
    const readOnly = fileNode({ id: "b", owner: anna, permission: "read" });
    const items = actionMenuItems([readOnly], env, (a) => ran.push(a.id));
    const ids = items.map((i) => i.id);
    expect(ids[0]).toBe("open");
    expect(ids).toContain("copy");
    const trash = items.find((i) => i.id === "trash");
    expect(trash).toMatchObject({ disabled: true, reason: "Je mag hier alleen kijken", danger: true, separatorBefore: true });
    expect(items.find((i) => i.id === "rename")?.separatorBefore).toBe(true);
    expect(items.find((i) => i.id === "cut")?.separatorBefore).toBe(false);
    items.find((i) => i.id === "copy")?.onSelect?.();
    expect(ran).toEqual(["copy"]);
  });

  it("registerAction voegt toe (fase 10) en haalt weer weg; een dubbel id mag niet", () => {
    const run = vi.fn();
    const convert: BrowserAction = {
      id: "test-omzetten",
      labelKey: "browser.actions.open",
      group: "convert",
      when: (sel) => ({ visible: sel.length > 0, enabled: true }),
      run,
    };
    const remove = registerAction(convert);
    expect(() => registerAction(convert)).toThrow();
    const items = actionMenuItems([fileNode({ id: "a" })], env, (a) => void a.run([], { ...env, ui: undefined as never }));
    const at = items.findIndex((i) => i.id === "test-omzetten");
    expect(at).toBeGreaterThan(items.findIndex((i) => i.id === "versions"));
    expect(at).toBeLessThan(items.findIndex((i) => i.id === "trash"));
    items[at].onSelect?.();
    expect(run).toHaveBeenCalled();
    remove();
    expect(findAction("test-omzetten")).toBeUndefined();
  });

  it("elke actie met een sneltoets heeft er een die de browser niet zelf nodig heeft", () => {
    const combos = browserActions().flatMap((a) => (typeof a.shortcut === "string" ? [a.shortcut] : [...(a.shortcut ?? [])]));
    for (const forbidden of ["mod+n", "mod+t", "mod+w", "mod+shift+n"]) expect(combos).not.toContain(forbidden);
    expect(combos).toContain("delete");
    expect(combos).toContain("mod+backspace");
    expect(combos).toContain("f2");
    expect(new Set(combos).size).toBe(combos.length);
  });
});

describe("de haken van office (fase 4)", () => {
  const on: OfficeStatus = {
    enabled: true,
    editing: true,
    state: "ok",
    edit_formats: ["docx", "xlsx", "pptx"],
    new_kinds: ["docx", "xlsx", "pptx"],
    max_bytes: 100,
  };
  const doc = fileNode({ id: "a", name: "Brief.DOCX", size: 10 });

  afterEach(() => officeStatus.set(null));

  it("zonder status (geen office, een oudere server, een fout): Bewerken en PDF zijn er niet", () => {
    officeStatus.set(null);
    expect(availability(action("edit"), [doc], env).visible).toBe(false);
    expect(availability(action("pdf"), [doc], env).visible).toBe(false);
    const ids = actionMenuItems([doc], env, () => {}).map((i) => i.id);
    expect(ids).not.toContain("edit");
    expect(ids).not.toContain("pdf");
  });

  it("office uit (ingesteld, maar uitgezet) of niet ingesteld: ook niet", () => {
    officeStatus.set({ ...on, enabled: false, editing: false });
    expect(availability(action("edit"), [doc], env).visible).toBe(false);
    officeStatus.set({ ...on, enabled: false, editing: false, state: "off" });
    expect(availability(action("edit"), [doc], env).visible).toBe(false);
  });

  it("Bewerken: één docx, xlsx of pptx met schrijfrecht, direct na Openen", () => {
    officeStatus.set(on);
    expect(availability(action("edit"), [doc], env)).toEqual({ visible: true, enabled: true });
    expect(availability(action("edit"), [fileNode({ id: "x", name: "Lijst.xlsx" })], env).visible).toBe(true);
    expect(availability(action("edit"), [fileNode({ id: "x", name: "notities.txt" })], env).visible).toBe(false);
    expect(availability(action("edit"), [fileNode({ id: "x", name: "docx" })], env).visible).toBe(false);
    expect(availability(action("edit"), [dirNode({ id: "d", name: "map.docx" })], env).visible).toBe(false);
    expect(availability(action("edit"), [doc, fileNode({ id: "b", name: "b.docx" })], env).visible).toBe(false);
    const readOnly = fileNode({ id: "r", name: "r.docx", owner: anna, permission: "read" });
    expect(availability(action("edit"), [readOnly], env).visible).toBe(false);
    const shared = fileNode({ id: "w", name: "w.docx", owner: anna, permission: "write" });
    expect(availability(action("edit"), [shared], env).enabled).toBe(true);
    const ids = actionMenuItems([doc], env, () => {}).map((i) => i.id);
    expect(ids.slice(0, 2)).toEqual(["open", "edit"]);
  });

  it("wat even niet kan, staat er uitgeschakeld met de reden", () => {
    officeStatus.set(on);
    const big = fileNode({ id: "g", name: "g.pptx", size: 101 });
    expect(availability(action("edit"), [big], env)).toEqual({
      visible: true,
      enabled: false,
      reason: "Te groot voor de editor (hooguit 100 MB)",
    });
    const trashed = fileNode({ id: "t", name: "t.docx", deleted_at: "2026-10-02T10:00:00Z" });
    expect(availability(action("edit"), [trashed], env).reason).toBe("Dit bestand staat in de prullenbak");
    officeStatus.set({ ...on, editing: false, state: "down" });
    expect(availability(action("edit"), [doc], env).reason).toMatch(/niet bereikbaar/);
  });

  it("de PDF-haak (O3): één bestand dat de editor omzet, in de groep omzetten vlak voor de prullenbak", () => {
    officeStatus.set(on);
    expect(availability(action("pdf"), [doc], env)).toEqual({ visible: true, enabled: true });
    expect(availability(action("pdf"), [fileNode({ id: "x", name: "notities.txt" })], env).visible).toBe(false);
    expect(availability(action("pdf"), [doc, fileNode({ id: "b", name: "b.docx" })], env).visible).toBe(false);
    const ids = actionMenuItems([doc], env, () => {}).map((i) => i.id);
    expect(ids.indexOf("pdf")).toBe(ids.indexOf("trash") - 1);
  });

  it("een andere status: wie het register toont, hoort het", () => {
    const heard = vi.fn();
    const stop = subscribeActions(heard);
    const before = actionsVersion();
    officeStatus.set(on);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(actionsVersion()).toBe(before + 1);
    officeStatus.set({ ...on });
    expect(heard).toHaveBeenCalledTimes(1);
    stop();
  });

  it("de sneltoets E is alleen voor Bewerken", () => {
    expect(action("edit").shortcut).toBe("e");
    const withE = browserActions().filter((a) => a.shortcut === "e" || (Array.isArray(a.shortcut) && a.shortcut.includes("e")));
    expect(withE.map((a) => a.id)).toEqual(["edit"]);
  });
});
