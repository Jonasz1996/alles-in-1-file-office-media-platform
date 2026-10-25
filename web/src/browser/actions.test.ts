import { beforeAll, describe, expect, it, vi } from "vitest";
import { loadAreas, resetI18nForTests } from "../i18n";
import {
  type ActionEnv,
  actionLabel,
  actionMenuItems,
  allowed,
  availability,
  type BrowserAction,
  browserActions,
  findAction,
  GROUPS,
  MAX_SHARE,
  registerAction,
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
    expect(browserActions().some((a) => a.group === "convert")).toBe(false);
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
