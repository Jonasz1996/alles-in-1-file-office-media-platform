import { describe, expect, it, vi } from "vitest";
import { formatCombo, isTypingTarget, keyToMove, matchKey, moveIndex, ShortcutRegistry } from "./keys";

type Fake = KeyboardEvent & { prevented: boolean };

function key(k: string, o: Partial<Record<"ctrl" | "meta" | "alt" | "shift", boolean>> & { code?: string; target?: unknown } = {}): Fake {
  const e = {
    key: k,
    code: o.code,
    ctrlKey: !!o.ctrl,
    metaKey: !!o.meta,
    altKey: !!o.alt,
    shiftKey: !!o.shift,
    isComposing: false,
    defaultPrevented: false,
    target: o.target ?? null,
    prevented: false,
    preventDefault() {
      e.prevented = true;
    },
  };
  return e as unknown as Fake;
}

describe("matchKey", () => {
  it("mod is Ctrl buiten een Mac en Cmd op een Mac", () => {
    expect(matchKey(key("a", { ctrl: true }), "mod+a", false)).toBe(true);
    expect(matchKey(key("a", { meta: true }), "mod+a", false)).toBe(false);
    expect(matchKey(key("a", { meta: true }), "mod+a", true)).toBe(true);
    expect(matchKey(key("a", { ctrl: true }), "mod+a", true)).toBe(false);
  });

  it("modifiers moeten precies kloppen", () => {
    expect(matchKey(key("a"), "a", false)).toBe(true);
    expect(matchKey(key("a", { ctrl: true }), "a", false)).toBe(false);
    expect(matchKey(key("A", { shift: true }), "a", false)).toBe(false);
    expect(matchKey(key("A", { shift: true }), "shift+a", false)).toBe(true);
    expect(matchKey(key("F10", { shift: true }), "shift+f10", false)).toBe(true);
  });

  it("Shift telt niet bij een leesteken zoals ?", () => {
    expect(matchKey(key("?", { shift: true }), "?", false)).toBe(true);
    expect(matchKey(key("?"), "?", false)).toBe(true);
  });

  it("namen en aliassen", () => {
    expect(matchKey(key("Delete"), "delete", false)).toBe(true);
    expect(matchKey(key("Escape"), "esc", false)).toBe(true);
    expect(matchKey(key("ArrowDown"), "down", false)).toBe(true);
    expect(matchKey(key(" "), "space", false)).toBe(true);
  });

  it("valt terug op de fysieke toets (Alt op een Mac geeft een ander teken)", () => {
    expect(matchKey(key("å", { alt: true, code: "KeyA" }), "alt+a", true)).toBe(true);
    expect(matchKey(key("&", { code: "Digit1" }), "1", false)).toBe(true);
  });
});

describe("formatCombo", () => {
  it("leesbaar per platform", () => {
    expect(formatCombo("mod+shift+a", false)).toBe("Ctrl+Shift+A");
    expect(formatCombo("mod+shift+a", true)).toBe("⇧⌘A");
    expect(formatCombo("delete", false)).toBe("Delete");
    expect(formatCombo("shift+f10", false)).toBe("Shift+F10");
    expect(formatCombo("arrowdown", false)).toBe("↓");
    expect(formatCombo("?", false)).toBe("?");
  });
});

describe("isTypingTarget", () => {
  it("velden en bewerkbare tekst, geen knoppen of vinkjes", () => {
    expect(isTypingTarget({ tagName: "INPUT", type: "text" } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: "INPUT", type: "search" } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: "INPUT", type: "checkbox" } as unknown as EventTarget)).toBe(false);
    expect(isTypingTarget({ tagName: "TEXTAREA" } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: "DIV", isContentEditable: true } as unknown as EventTarget)).toBe(true);
    expect(isTypingTarget({ tagName: "BUTTON" } as unknown as EventTarget)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

describe("ShortcutRegistry", () => {
  it("global werkt meteen; een andere scope pas als hij op de stapel staat", () => {
    const r = new ShortcutRegistry(() => false);
    const g = vi.fn();
    const b = vi.fn();
    r.register("global", () => [{ combo: "?", run: g, label: "Hulp" }]);
    r.register("browser", () => [{ combo: "delete", run: b, label: "Weg" }]);
    expect(r.dispatch(key("Delete"))).toBe(false);
    expect(b).not.toHaveBeenCalled();
    const pop = r.pushScope("browser");
    const e = key("Delete");
    expect(r.dispatch(e)).toBe(true);
    expect(e.prevented).toBe(true);
    expect(b).toHaveBeenCalledTimes(1);
    expect(r.dispatch(key("?"))).toBe(true);
    expect(g).toHaveBeenCalledTimes(1);
    pop();
    pop(); // twee keer weghalen doet niets extra
    expect(r.scopes()).toEqual(["global"]);
  });

  it("een dialoog houdt alles eronder tegen", () => {
    const r = new ShortcutRegistry(() => false);
    const g = vi.fn();
    const d = vi.fn();
    r.register("global", () => [{ combo: "?", run: g, label: "Hulp" }]);
    r.register("dialog", () => [{ combo: "mod+enter", run: d, label: "Opslaan" }]);
    const pop = r.pushScope("dialog");
    expect(r.dispatch(key("?"))).toBe(false);
    expect(r.dispatch(key("Enter", { ctrl: true }))).toBe(true);
    expect(d).toHaveBeenCalledTimes(1);
    expect(r.active().map((a) => a.label)).toEqual(["Opslaan"]);
    pop();
    expect(r.active().map((a) => a.label)).toEqual(["Hulp"]);
  });

  it("de bovenste scope en de nieuwste registratie gaan voor", () => {
    const r = new ShortcutRegistry(() => false);
    const calls: string[] = [];
    r.register("global", () => [{ combo: "x", run: () => calls.push("global") }]);
    r.register("viewer", () => [{ combo: "x", run: () => calls.push("viewer-oud") }]);
    r.register("viewer", () => [{ combo: "x", run: () => calls.push("viewer-nieuw") }]);
    r.pushScope("viewer");
    r.dispatch(key("x"));
    expect(calls).toEqual(["viewer-nieuw"]);
  });

  it("niet in een invoerveld, tenzij allowInInput; when() kan hem uitzetten", () => {
    const r = new ShortcutRegistry(() => false);
    const a = vi.fn();
    const esc = vi.fn();
    let on = false;
    r.register("global", () => [
      { combo: "a", run: a, when: () => on },
      { combo: "escape", run: esc, allowInInput: true },
    ]);
    const input = { tagName: "INPUT", type: "text" };
    expect(r.dispatch(key("a", { target: input }))).toBe(false);
    expect(r.dispatch(key("a"))).toBe(false); // when() geeft false
    on = true;
    expect(r.dispatch(key("a"))).toBe(true);
    expect(r.dispatch(key("Escape", { target: input }))).toBe(true);
    expect(a).toHaveBeenCalledTimes(1);
    expect(esc).toHaveBeenCalledTimes(1);
  });

  it("meldt wijzigingen aan wie luistert (de hulp)", () => {
    const r = new ShortcutRegistry(() => false);
    const l = vi.fn();
    const off = r.subscribe(l);
    const unregister = r.register("global", () => []);
    r.pushScope("menu")();
    unregister();
    off();
    r.register("global", () => []);
    expect(l).toHaveBeenCalledTimes(4);
  });
});

describe("moveIndex", () => {
  it("een lijst", () => {
    expect(moveIndex(0, "down", 5)).toBe(1);
    expect(moveIndex(4, "down", 5)).toBe(4);
    expect(moveIndex(0, "up", 5)).toBe(0);
    expect(moveIndex(2, "home", 5)).toBe(0);
    expect(moveIndex(2, "end", 5)).toBe(4);
    expect(moveIndex(2, "left", 5)).toBe(2);
    expect(moveIndex(0, "pagedown", 25, 1, 10)).toBe(10);
    expect(moveIndex(20, "pagedown", 25, 1, 10)).toBe(24);
    expect(moveIndex(5, "pageup", 25, 1, 10)).toBe(0);
  });

  it("zonder focus begint elke beweging bovenaan, End onderaan; leeg geeft -1", () => {
    expect(moveIndex(-1, "down", 5)).toBe(0);
    expect(moveIndex(-1, "up", 5)).toBe(0);
    expect(moveIndex(-1, "end", 5)).toBe(4);
    expect(moveIndex(0, "down", 0)).toBe(-1);
  });

  it("een raster met een kortere laatste rij", () => {
    // 4 kolommen, 10 cellen: rijen 0-3, 4-7, 8-9
    expect(moveIndex(1, "right", 10, 4)).toBe(2);
    expect(moveIndex(1, "left", 10, 4)).toBe(0);
    expect(moveIndex(1, "down", 10, 4)).toBe(5);
    expect(moveIndex(6, "down", 10, 4)).toBe(9); // geen cel eronder: de laatste
    expect(moveIndex(9, "down", 10, 4)).toBe(9);
    expect(moveIndex(9, "up", 10, 4)).toBe(5);
    expect(moveIndex(2, "up", 10, 4)).toBe(2);
    expect(moveIndex(9, "right", 10, 4)).toBe(9);
    expect(moveIndex(9, "pageup", 10, 4, 10)).toBe(1); // dezelfde kolom bovenaan
  });
});

describe("keyToMove", () => {
  it("pijlen, Home/End en PgUp/PgDn; niet met Alt", () => {
    const k = (key: string, altKey = false) => ({ key, altKey, ctrlKey: false, metaKey: false });
    expect(keyToMove(k("ArrowUp"))).toBe("up");
    expect(keyToMove(k("ArrowRight"))).toBe("right");
    expect(keyToMove(k("End"))).toBe("end");
    expect(keyToMove(k("PageDown"))).toBe("pagedown");
    expect(keyToMove(k("ArrowUp", true))).toBeNull();
    expect(keyToMove(k("a"))).toBeNull();
  });
});
