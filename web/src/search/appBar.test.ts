import { describe, expect, it, vi } from "vitest";
import { formatCombo, matchKey, ShortcutRegistry } from "../ui/keys";
import { ariaKeys, MAX_QUERY, SEARCH_KEY, searchShortcut, searchTarget } from "./appBar";

/** Een toets zoals de browser hem geeft, met een doel (een element of niets). */
function key(
  k: string,
  o: { ctrl?: boolean; meta?: boolean; alt?: boolean; shift?: boolean; target?: unknown } = {},
): KeyboardEvent & { prevented: boolean } {
  const e = {
    key: k,
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
  return e as unknown as KeyboardEvent & { prevented: boolean };
}

const INPUT = { tagName: "INPUT", type: "text" };
const SEARCHBOX = { tagName: "INPUT", type: "search" };

describe("het zoekveld in de balk bovenaan (Keuze 45)", () => {
  it("Enter gaat naar /zoeken?q=…, zonder spaties eromheen; zonder woorden nergens heen", () => {
    expect(searchTarget("vakantie")).toBe("/zoeken?q=vakantie");
    expect(searchTarget("  rapport 2024  ")).toBe("/zoeken?q=rapport+2024");
    expect(searchTarget("a&b#c")).toBe("/zoeken?q=a%26b%23c");
    expect(searchTarget("")).toBeNull();
    expect(searchTarget("   ")).toBeNull();
  });

  it("de pagina /zoeken (Z4) leest q terug zoals het veld hem schrijft", async () => {
    const { readQuery } = await import("./query");
    const href = searchTarget(" één & twee ") ?? "";
    expect(readQuery(href.slice(href.indexOf("?")))).toEqual({ q: "één & twee" });
  });

  it("hooguit 200 tekens, zoals de API (search_query_too_long)", () => {
    expect(MAX_QUERY).toBe(200);
  });

  it("Ctrl+K, op een Mac Cmd+K; zo noemt de hulp hem en zo noemt een schermlezer hem", () => {
    expect(SEARCH_KEY).toBe("mod+k");
    expect(formatCombo(SEARCH_KEY, false)).toBe("Ctrl+K");
    expect(formatCombo(SEARCH_KEY, true)).toBe("⌘K");
    expect(ariaKeys(false)).toBe("Control+K");
    expect(ariaKeys(true)).toBe("Meta+K");
  });
});

describe("de sneltoets Ctrl/Cmd+K", () => {
  function setup(mac: boolean) {
    const reg = new ShortcutRegistry(() => mac);
    const focus = vi.fn();
    reg.register("global", () => [searchShortcut(focus, "Naar het zoekveld")]);
    return { reg, focus };
  }

  it("Ctrl+K zet de focus op het veld en neemt de toets (geen zoekbalk van de browser)", () => {
    const { reg, focus } = setup(false);
    const e = key("k", { ctrl: true });
    expect(reg.dispatch(e)).toBe(true);
    expect(e.prevented).toBe(true);
    expect(focus).toHaveBeenCalledTimes(1);
    // Met Caps Lock aan geeft de browser "K"
    expect(reg.dispatch(key("K", { ctrl: true }))).toBe(true);
  });

  it("op een Mac Cmd+K, en Ctrl+K daar niet", () => {
    const { reg, focus } = setup(true);
    expect(reg.dispatch(key("k", { ctrl: true }))).toBe(false);
    expect(reg.dispatch(key("k", { meta: true }))).toBe(true);
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it("ook als je in een ander veld typt; in het zoekveld zelf opnieuw (dan selecteert hij)", () => {
    const { reg, focus } = setup(false);
    expect(reg.dispatch(key("k", { ctrl: true, target: INPUT }))).toBe(true);
    expect(reg.dispatch(key("k", { ctrl: true, target: SEARCHBOX }))).toBe(true);
    expect(focus).toHaveBeenCalledTimes(2);
  });

  it("niet bij k alleen, Alt+K, Ctrl+Shift+K of Ctrl+Alt+K (AltGr)", () => {
    const { reg, focus } = setup(false);
    for (const e of [
      key("k"),
      key("k", { alt: true }),
      key("K", { ctrl: true, shift: true }),
      key("k", { ctrl: true, alt: true }),
      key("k", { meta: true }),
    ]) {
      expect(reg.dispatch(e)).toBe(false);
      expect(e.prevented).toBe(false);
    }
    expect(focus).not.toHaveBeenCalled();
  });

  it("onder een dialoog, een menu of de viewer niet: die houden de globale toetsen tegen", () => {
    for (const scopes of [["dialog"], ["menu"], ["dialog", "viewer"], ["browser", "dialog", "share"]]) {
      const { reg, focus } = setup(false);
      const pops = scopes.map((s) => reg.pushScope(s));
      expect(reg.dispatch(key("k", { ctrl: true }))).toBe(false);
      for (const pop of pops.reverse()) pop();
      expect(reg.dispatch(key("k", { ctrl: true }))).toBe(true);
      expect(focus).toHaveBeenCalledTimes(1);
    }
  });

  it("op een pagina met een eigen scope (Bestanden, beheer) werkt hij wel", () => {
    const { reg, focus } = setup(false);
    reg.pushScope("browser");
    expect(reg.dispatch(key("k", { ctrl: true }))).toBe(true);
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it("botst niet met k (afspelen in de viewer en de speler) of met / (het zoekveld van Bestanden)", () => {
    expect(matchKey(key("k", { ctrl: true }), "k", false)).toBe(false);
    expect(matchKey(key("k", { meta: true }), "k", true)).toBe(false);
    expect(matchKey(key("k"), SEARCH_KEY, false)).toBe(false);
    expect(matchKey(key("/"), SEARCH_KEY, false)).toBe(false);
    // Een pagina die zelf een k heeft, krijgt die gewoon (de gewone k gaat niet naar het veld)
    const { reg, focus } = setup(false);
    const play = vi.fn();
    reg.pushScope("viewer");
    reg.register("viewer", () => [{ combo: [" ", "k"], run: play }]);
    expect(reg.dispatch(key("k"))).toBe(true);
    expect(play).toHaveBeenCalledTimes(1);
    expect(focus).not.toHaveBeenCalled();
  });

  it("staat in de hulp (?) bij de globale toetsen", () => {
    const { reg } = setup(false);
    expect(reg.active()).toEqual([{ scope: "global", combo: "mod+k", label: "Naar het zoekveld" }]);
  });
});
