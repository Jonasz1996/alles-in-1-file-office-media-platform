import { describe, expect, it, vi } from "vitest";
import { consumesKey, formatCombo, isTypingTarget, keyToMove, matchKey, moveIndex, ShortcutRegistry } from "./keys";

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

describe("AZERTY en andere toetsenborden", () => {
  it("een gewone letter in key gaat voor de fysieke toets", () => {
    // AZERTY: de toets op de plek van A (KeyA) geeft q
    expect(matchKey(key("q", { ctrl: true, code: "KeyA" }), "mod+a", false)).toBe(false);
    expect(matchKey(key("q", { code: "KeyA" }), "a", false)).toBe(false);
    expect(matchKey(key("q", { ctrl: true, code: "KeyA" }), "mod+q", false)).toBe(true);
    expect(matchKey(key("a", { code: "KeyQ" }), "a", false)).toBe(true);
    // Niet-Latijns (Russisch: ф op KeyA) en Alt op een Mac: wel de fysieke toets
    expect(matchKey(key("ф", { ctrl: true, code: "KeyA" }), "mod+a", false)).toBe(true);
  });
});

describe("consumesKey", () => {
  const el = (tagName: string, o: { type?: string; attrs?: string[] } = {}) =>
    ({ tagName, type: o.type, hasAttribute: (n: string) => (o.attrs ?? []).includes(n) }) as unknown as EventTarget;

  it("schuif, keuzerondjes, cijfers en media met bediening houden de pijltjes", () => {
    for (const type of ["range", "radio", "number", "date"]) {
      expect(consumesKey(el("INPUT", { type }), key("ArrowLeft"))).toBe(true);
    }
    expect(consumesKey(el("VIDEO", { attrs: ["controls"] }), key("ArrowRight"))).toBe(true);
    expect(consumesKey(el("VIDEO"), key("ArrowRight"))).toBe(false);
    expect(consumesKey(el("INPUT", { type: "checkbox" }), key("ArrowLeft"))).toBe(false);
    expect(consumesKey(el("DIV"), key("Home"))).toBe(false);
    expect(consumesKey(el("INPUT", { type: "range" }), key("ArrowLeft", { ctrl: true }))).toBe(false);
  });

  it("knoppen, links en vinkjes houden Enter en Spatie", () => {
    expect(consumesKey(el("BUTTON"), key("Enter"))).toBe(true);
    expect(consumesKey(el("A", { attrs: ["href"] }), key(" "))).toBe(true);
    expect(consumesKey(el("A"), key("Enter"))).toBe(false);
    expect(consumesKey(el("INPUT", { type: "checkbox" }), key(" "))).toBe(true);
    expect(consumesKey(el("BUTTON"), key("ArrowDown"))).toBe(false);
    expect(consumesKey(el("BUTTON"), key("Delete"))).toBe(false);
  });

  it("dan neemt een sneltoets de toets niet af", () => {
    const r = new ShortcutRegistry(() => false);
    const run = vi.fn();
    r.register("global", () => [{ combo: "arrowleft", run }]);
    for (const target of [el("INPUT", { type: "range" }), el("INPUT", { type: "radio" }), el("VIDEO", { attrs: ["controls"] })]) {
      const e = key("ArrowLeft", { target });
      expect(r.dispatch(e)).toBe(false);
      expect(e.prevented).toBe(false);
    }
    expect(run).not.toHaveBeenCalled();
    expect(r.dispatch(key("ArrowLeft", { target: el("DIV") }))).toBe(true);
  });
});

describe("de hulp vanuit een menu", () => {
  it("active({ignore}) slaat het menu over dat nog op de stapel staat", () => {
    const r = new ShortcutRegistry(() => false);
    r.register("global", () => [{ combo: "?", run: () => {}, label: "Sneltoetsen tonen" }]);
    const pop = r.pushScope("menu");
    expect(r.active()).toEqual([]);
    expect(r.active({ ignore: ["menu"] }).map((a) => a.combo)).toEqual(["?"]);
    pop();
  });

  it("een scope in een dialoog die erboven staat, krijgt de toets", () => {
    // Zo staat de stapel als Dialog "dialog" in een layout-effect zet en het kind daarna
    // useScope("viewer") doet
    const r = new ShortcutRegistry(() => false);
    const next = vi.fn();
    r.register("viewer", () => [{ combo: "arrowright", run: next }]);
    r.pushScope("dialog");
    r.pushScope("viewer");
    expect(r.dispatch(key("ArrowRight"))).toBe(true);
    expect(next).toHaveBeenCalledTimes(1);
  });
});
