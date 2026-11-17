import { describe, expect, it } from "vitest";
import appStatusSource from "../pwa/AppStatus.tsx?raw";
import { ShortcutRegistry } from "../ui/keys";
import helpSource from "../ui/ShortcutHelp.tsx?raw";
import traySource from "../upload/UploadTray.tsx?raw";
import mediaViewSource from "../viewer/MediaView.tsx?raw";
import pageViewerSource from "../viewer/PageViewer.tsx?raw";
import viewerSource from "../viewer/Viewer.tsx?raw";
import zoomSource from "../viewer/ZoomView.tsx?raw";
import {
  ACTIONS,
  actionsFor,
  bindings,
  chapterTarget,
  digitOf,
  KEYMAP,
  type PlayerAction,
  percentTime,
  scopeFor,
  seekBy,
  VIEWER_ACTIONS,
  volumeStep,
} from "./keys";

type Fake = KeyboardEvent & { prevented: boolean };

function key(k: string, o: { shift?: boolean; ctrl?: boolean; code?: string; target?: unknown } = {}): Fake {
  const e = {
    key: k,
    code: o.code,
    ctrlKey: !!o.ctrl,
    metaKey: false,
    altKey: false,
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

/** Een register met de speler erin, en wat er liep. */
function setup(where: "watch" | "viewer") {
  const reg = new ShortcutRegistry(() => false);
  const ran: PlayerAction[] = [];
  reg.pushScope(scopeFor(where));
  reg.register(scopeFor(where), () =>
    bindings(where, { run: (a) => ran.push(a), label: (a) => a }),
  );
  return { reg, ran };
}

describe("de tabel van Keuze 33", () => {
  it("elke toets op /kijken doet zijn actie", () => {
    const cases: [string, PlayerAction, { shift?: boolean }?][] = [
      [" ", "play"],
      ["k", "play"],
      ["j", "back10"],
      ["l", "forward10"],
      ["ArrowLeft", "back5"],
      ["ArrowRight", "forward5"],
      ["ArrowUp", "volumeUp"],
      ["ArrowDown", "volumeDown"],
      ["m", "mute"],
      ["f", "fullscreen"],
      ["c", "subtitles"],
      ["a", "audio"],
      ["p", "pip"],
      ["PageUp", "prevChapter"],
      ["PageDown", "nextChapter"],
      ["0", "percent"],
      ["5", "percent"],
      ["9", "percent"],
      ["Home", "begin"],
      ["End", "end"],
      ["n", "next"],
      ["i", "info"],
      ["Escape", "escape"],
    ];
    for (const [k, action] of cases) {
      const { reg, ran } = setup("watch");
      const e = key(k);
      expect(reg.dispatch(e), k).toBe(true);
      expect(ran, k).toEqual([action]);
      expect(e.prevented).toBe(true);
    }
  });

  it("in de viewer alleen Spatie, K, J, L, M, C, A, P en 0–9", () => {
    expect([...VIEWER_ACTIONS].sort()).toEqual(["audio", "back10", "forward10", "mute", "percent", "pip", "play", "subtitles"]);
    for (const k of [" ", "k", "j", "l", "m", "c", "a", "p", "0", "7"]) {
      const { reg, ran } = setup("viewer");
      expect(reg.dispatch(key(k)), k).toBe(true);
      expect(ran.length).toBe(1);
    }
    for (const k of ["ArrowLeft", "ArrowRight", "Home", "End", "f", "i", "n", "PageUp", "Escape", "ArrowUp"]) {
      const { reg, ran } = setup("viewer");
      expect(reg.dispatch(key(k)), k).toBe(false);
      expect(ran).toEqual([]);
    }
  });

  it("niets als de focus in een invoerveld staat", () => {
    const input = { tagName: "INPUT", type: "text" };
    const area = { tagName: "TEXTAREA" };
    const editable = { tagName: "DIV", isContentEditable: true };
    for (const target of [input, area, editable]) {
      for (const k of [" ", "k", "j", "m", "f", "c", "n", "5", "ArrowLeft", "Escape"]) {
        const { reg, ran } = setup("watch");
        expect(reg.dispatch(key(k, { target })), k).toBe(false);
        expect(ran).toEqual([]);
      }
    }
  });

  it("niets als er een menu of dialoog bovenop staat", () => {
    for (const scope of ["menu", "dialog"]) {
      const { reg, ran } = setup("watch");
      reg.pushScope(scope);
      for (const k of [" ", "k", "f", "ArrowLeft", "n"]) expect(reg.dispatch(key(k))).toBe(false);
      expect(ran).toEqual([]);
    }
  });

  it("een knop met focus houdt Spatie zelf (klikken), maar de letters werken", () => {
    const button = { tagName: "BUTTON", hasAttribute: () => false };
    const { reg, ran } = setup("watch");
    expect(reg.dispatch(key(" ", { target: button }))).toBe(false);
    expect(reg.dispatch(key("k", { target: button }))).toBe(true);
    expect(ran).toEqual(["play"]);
  });

  it("de volumeschuif (range) houdt de pijltjes, de letters werken", () => {
    const range = { tagName: "INPUT", type: "range" };
    const { reg, ran } = setup("watch");
    expect(reg.dispatch(key("ArrowUp", { target: range }))).toBe(false);
    expect(reg.dispatch(key("m", { target: range }))).toBe(true);
    expect(ran).toEqual(["mute"]);
  });

  it("Shift+N is van de app, N van de speler; Ctrl+N/T/W nooit", () => {
    const { reg, ran } = setup("watch");
    expect(reg.dispatch(key("N", { shift: true }))).toBe(false);
    expect(reg.dispatch(key("n", { ctrl: true }))).toBe(false);
    expect(reg.dispatch(key("t", { ctrl: true }))).toBe(false);
    expect(reg.dispatch(key("w", { ctrl: true }))).toBe(false);
    expect(ran).toEqual([]);
    for (const combos of Object.values(KEYMAP)) {
      for (const c of combos) expect(c).not.toMatch(/(mod|ctrl|meta)\+[ntw]$/);
    }
  });

  it("when: N alleen met een volgende aflevering", () => {
    const reg = new ShortcutRegistry(() => false);
    const ran: string[] = [];
    reg.pushScope("player");
    reg.register("player", () =>
      bindings("watch", { run: (a) => ran.push(a), label: (a) => a, can: (a) => a !== "next" }),
    );
    expect(reg.dispatch(key("n"))).toBe(false);
    expect(reg.dispatch(key("k"))).toBe(true);
    expect(ran).toEqual(["play"]);
  });

  it("cijfers ook via de fysieke toets", () => {
    expect(digitOf({ key: "7" })).toBe(7);
    expect(digitOf({ key: "è", code: "Digit7" })).toBe(7);
    expect(digitOf({ key: "x", code: "KeyX" })).toBeNull();
  });
});

// --- de scopes naast elkaar (Keuze 33, review R19) ---

/** De combinaties uit de bron van een component (combo: "x" of combo: ["x", "y"]). */
function combosIn(source: string): string[] {
  const out: string[] = [];
  for (const m of source.matchAll(/combo:\s*(\[[^\]]*\]|"[^"]*")/g)) {
    for (const s of m[1].matchAll(/"([^"]*)"/g)) out.push(s[1].toLowerCase());
  }
  return out;
}

/** Bewust overschreven, met de reden. */
const OVERRIDES: Record<string, string> = {
  // De <video> van fase 3 (NativeMedia) is de fallback van de speler: nooit samen in beeld
  "viewer: ": "NativeMedia",
  "viewer:k": "NativeMedia",
  "viewer:j": "NativeMedia",
  "viewer:l": "NativeMedia",
  "viewer:m": "NativeMedia",
  // ZoomView en PageViewer (0, 1) staan alleen bij een foto of PDF, nooit bij een video
  "viewer:0": "ZoomView/PageViewer",
  "viewer:1": "ZoomView/PageViewer",
};

describe("de scopes global, viewer, player en /kijken", () => {
  const global = [...combosIn(helpSource), ...combosIn(appStatusSource), ...combosIn(traySource)];
  const viewer = combosIn(viewerSource);
  const viewerOthers = [...combosIn(mediaViewSource), ...combosIn(zoomSource), ...combosIn(pageViewerSource)];
  const player = (where: "watch" | "viewer") => actionsFor(where).flatMap((a) => [...KEYMAP[a]]);

  it("de bron leest goed", () => {
    expect(global).toEqual(expect.arrayContaining(["?", "shift+n", "shift+p"]));
    expect(viewer).toEqual(expect.arrayContaining(["escape", "arrowleft", "home", "end", "f", "i", "d", "e", "?"]));
  });

  it("op /kijken: player plus global, zonder dubbele", () => {
    const watch = player("watch");
    expect(new Set(watch).size).toBe(watch.length);
    for (const c of watch) expect(global, c).not.toContain(c);
    // "?" is de lijst van de app zelf: de speler zet hem niet
    expect(watch).not.toContain("?");
  });

  it("in de viewer: de speler botst niet met de viewer, en alleen bewust met de rest", () => {
    const mine = player("viewer");
    expect(new Set(mine).size).toBe(mine.length);
    for (const c of mine) {
      expect(viewer, c).not.toContain(c);
      if (viewerOthers.includes(c)) expect(OVERRIDES[`viewer:${c}`], c).toBeTruthy();
    }
    // ←, →, Home, End, F, I, D, E en ? blijven van de viewer
    for (const c of ["arrowleft", "arrowright", "home", "end", "f", "i", "d", "e", "?"]) expect(mine).not.toContain(c);
  });

  it("elke bewuste overschrijving bestaat echt", () => {
    for (const k of Object.keys(OVERRIDES)) {
      const c = k.slice("viewer:".length);
      expect(player("viewer")).toContain(c);
      expect(viewerOthers).toContain(c);
    }
  });

  it("elke actie heeft een toets", () => {
    for (const a of ACTIONS) expect(KEYMAP[a].length).toBeGreaterThan(0);
  });
});

describe("de rekensommen", () => {
  it("0–9 is 0–90 %", () => {
    expect(percentTime(0, 200)).toBe(0);
    expect(percentTime(5, 200)).toBe(100);
    expect(percentTime(9, 200)).toBe(180);
    expect(percentTime(3, Number.NaN)).toBe(0);
  });

  it("springen blijft binnen de film", () => {
    expect(seekBy(3, -10, 100)).toBe(0);
    expect(seekBy(95, 10, 100)).toBe(100);
    expect(seekBy(50, 5, Number.NaN)).toBe(55);
  });

  it("hoofdstukken: volgend, vorig, en binnen 3 s het vorige", () => {
    const starts = [0, 60, 120];
    expect(chapterTarget(starts, 10, 1)).toBe(60);
    expect(chapterTarget(starts, 130, 1)).toBeNull();
    expect(chapterTarget(starts, 90, -1)).toBe(60);
    expect(chapterTarget(starts, 61, -1)).toBe(0);
    expect(chapterTarget(starts, 1, -1)).toBe(0);
  });

  it("volume in stappen van 10 %", () => {
    expect(volumeStep(0.5, 1)).toBe(0.6);
    expect(volumeStep(0.95, 1)).toBe(1);
    expect(volumeStep(0.05, -1)).toBe(0);
  });
});
