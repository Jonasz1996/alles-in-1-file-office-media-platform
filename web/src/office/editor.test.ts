import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { loadAreas, resetI18nForTests } from "../i18n";
import {
  BUSY_MAX_MS,
  BUSY_RETRY_MS,
  type Deps,
  type EditorEvents,
  EditorSession,
  initialState,
  isLeaveKey,
  READY_MS,
  SAVING_MS,
  sessionBody,
  statusText,
  type Timers,
} from "./editor";
import { GATE_RENEW_MS, keepGate } from "./gate";
import { type DocEditor, type DocsApi, loadApi, resetApiForTests, type ScriptHost } from "./loadApi";
import type { SessionOut } from "./types";

beforeAll(async () => {
  resetI18nForTests("nl");
  await loadAreas(["common", "office"]);
});

/** Een klok die alleen loopt als de test dat zegt. */
class Clock implements Timers {
  now = 1_000_000;
  private seq = 0;
  private due = new Map<number, { at: number; fn: () => void }>();

  set(fn: () => void, ms: number): unknown {
    const id = ++this.seq;
    this.due.set(id, { at: this.now + ms, fn });
    return id;
  }

  clear(handle: unknown): void {
    this.due.delete(handle as number);
  }

  async advance(ms: number): Promise<void> {
    const end = this.now + ms;
    for (;;) {
      await flush(); // eerst wat er al loopt een klok laten zetten
      const next = [...this.due.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > end) break;
      this.due.delete(next[0]);
      this.now = next[1].at;
      next[1].fn();
      await flush();
    }
    this.now = end;
    await flush();
  }

  pending(): number {
    return this.due.size;
  }
}

/** Alle microtaken laten lopen (een echte macrotaak; de klok hierboven is nep). */
async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function session(key = "k1", extra: Partial<SessionOut> = {}): SessionOut {
  return {
    api_url: "/office/web-apps/apps/api/documents/api.js",
    config: { document: { key, title: "verslag.docx" }, token: `t-${key}` },
    key,
    stale: false,
    gate_seconds: 7200,
    ...extra,
  };
}

interface World {
  deps: Deps;
  clock: Clock;
  /** modified_at van het bestand zoals GET …/files/{id} hem geeft; saved() zet hem verder */
  file: { modified: string };
  open: ReturnType<typeof vi.fn<() => Promise<SessionOut>>>;
  editors: { destroyEditor: ReturnType<typeof vi.fn>; refreshFile: ReturnType<typeof vi.fn> }[];
  events: EditorEvents[];
  stopGate: ReturnType<typeof vi.fn>;
  ed: EditorSession;
}

function world(open?: () => Promise<SessionOut>): World {
  const clock = new Clock();
  const editors: World["editors"] = [];
  const events: EditorEvents[] = [];
  const stopGate = vi.fn();
  const openFn = vi.fn(open ?? (async () => session()));
  const file = { modified: "2026-10-27T09:00:00Z" };
  const deps: Deps = {
    open: openFn,
    modifiedAt: vi.fn(async () => file.modified),
    mount: vi.fn(async (_s: SessionOut, ev: EditorEvents) => {
      events.push(ev);
      const made = { destroyEditor: vi.fn(), refreshFile: vi.fn() };
      editors.push(made);
      return made as DocEditor;
    }),
    saveNow: vi.fn(async () => {}),
    keepGate: vi.fn(() => stopGate),
    focusFrame: vi.fn(),
    close: vi.fn(),
    reload: vi.fn(),
    timers: clock,
    now: () => clock.now,
  };
  return { deps, clock, file, open: openFn, editors, events, stopGate, ed: new EditorSession("n1", deps) };
}

/** Het bestand kreeg nieuwe inhoud (een opslag): modified_at een minuut verder. */
function saved(w: World): void {
  w.file.modified = new Date(Date.parse(w.file.modified) + 60_000).toISOString();
}

const busy = () => new ApiError(409, "Dit document wordt net opgeslagen", "office_busy");

async function ready(w: World): Promise<EditorEvents> {
  await w.ed.start();
  const ev = w.events[w.events.length - 1];
  ev.onAppReady();
  ev.onDocumentReady();
  return ev;
}

describe("openen", () => {
  it("vraagt de sessie, maakt de editor, en is na onDocumentReady klaar met de focus in de iframe", async () => {
    const w = world();
    expect(w.ed.getState().phase).toBe("opening");
    await w.ed.start();
    expect(w.ed.getState()).toMatchObject({ phase: "loading", key: "k1", title: "verslag.docx", connection: "connecting" });
    expect(statusText(w.ed.getState())).toBe("Verbinden…");
    expect(w.deps.keepGate).toHaveBeenCalledTimes(1);
    w.events[0].onAppReady();
    w.events[0].onDocumentReady();
    expect(w.ed.getState()).toMatchObject({ phase: "ready", connection: "online" });
    expect(w.deps.focusFrame).toHaveBeenCalledTimes(1);
    expect(statusText(w.ed.getState())).toBe("Bewerken");
  });

  it("de events zijn die van Keuze 32, en nooit onRequestSaveAs (A16)", async () => {
    const w = world();
    await w.ed.start();
    expect(Object.keys(w.events[0]).sort()).toEqual(
      [
        "onAppReady",
        "onDocumentReady",
        "onDocumentStateChange",
        "onError",
        "onRequestClose",
        "onRequestRefreshFile",
        "onWarning",
      ].sort(),
    );
    expect("onRequestSaveAs" in w.events[0]).toBe(false);
  });

  it("stale: een melding dat het bestand buiten de editor veranderde", async () => {
    const w = world(async () => session("k1", { stale: true }));
    await w.ed.start();
    expect(w.ed.getState().notices).toEqual([
      { kind: "stale", text: expect.stringContaining("buiten de editor veranderd"), reload: false },
    ]);
  });

  it("office_busy: elke 3 s opnieuw, met een melding, en daarna gewoon open", async () => {
    let calls = 0;
    const w = world(async () => {
      calls += 1;
      if (calls < 3) throw busy();
      return session();
    });
    const started = w.ed.start();
    await flush();
    expect(w.ed.getState().notices.map((n) => n.kind)).toEqual(["busy"]);
    await w.clock.advance(BUSY_RETRY_MS - 1);
    expect(calls).toBe(1);
    await w.clock.advance(1);
    expect(calls).toBe(2);
    await w.clock.advance(BUSY_RETRY_MS);
    await started;
    expect(calls).toBe(3);
    expect(w.ed.getState().phase).toBe("loading");
    expect(w.ed.getState().notices).toEqual([]);
  });

  it("office_busy langer dan 30 s: dan de fout zelf, en niet meer proberen", async () => {
    const w = world(async () => {
      throw busy();
    });
    const started = w.ed.start();
    await w.clock.advance(BUSY_MAX_MS + BUSY_RETRY_MS * 3);
    await started;
    expect(w.open).toHaveBeenCalledTimes(BUSY_MAX_MS / BUSY_RETRY_MS + 1);
    expect(w.ed.getState().phase).toBe("failed");
    expect(w.ed.getState().notices).toEqual([{ kind: "failed", text: "Dit document wordt net opgeslagen", reload: false }]);
    expect(w.deps.mount).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError(429, "Er bewerken nu te veel mensen tegelijk (hooguit 20)", "office_too_many", { max: 20 }), false],
    [new ApiError(422, "Dit bestand kwam niet door de controle", "office_unsafe"), false],
    [new ApiError(503, "De editor is nu niet bereikbaar", "office_unavailable"), true],
  ])("een fout bij openen (%s): een melding, de editor komt er niet", async (error, reload) => {
    const w = world(async () => {
      throw error;
    });
    await w.ed.start();
    expect(w.ed.getState().phase).toBe("failed");
    expect(w.ed.getState().notices).toEqual([{ kind: "failed", text: error.message, reload }]);
    expect(w.open).toHaveBeenCalledTimes(1);
    expect(w.deps.keepGate).not.toHaveBeenCalled();
  });

  it("geen onAppReady binnen 20 s: de editor één keer opnieuw, daarna een melding (A22)", async () => {
    const w = world();
    await w.ed.start();
    await w.clock.advance(READY_MS);
    expect(w.deps.mount).toHaveBeenCalledTimes(2);
    expect(w.editors[0].destroyEditor).toHaveBeenCalledTimes(1);
    expect(w.ed.getState().phase).toBe("loading");
    await w.clock.advance(READY_MS);
    expect(w.deps.mount).toHaveBeenCalledTimes(2);
    expect(w.ed.getState().phase).toBe("failed");
    expect(w.ed.getState().notices).toEqual([{ kind: "not_ready", text: expect.stringContaining("niet klaar"), reload: true }]);
  });

  it("met onAppReady op tijd: geen tweede editor", async () => {
    const w = world();
    await w.ed.start();
    w.events[0].onAppReady();
    await w.clock.advance(READY_MS * 2);
    expect(w.deps.mount).toHaveBeenCalledTimes(1);
  });
});

describe("tijdens het bewerken", () => {
  it("office.presence: Bewerken met n anderen (count telt jezelf mee)", async () => {
    const w = world();
    await ready(w);
    w.ed.onServerEvent({ type: "office.presence", data: { node_id: "n1", count: 3 } });
    expect(statusText(w.ed.getState())).toBe("Bewerken met 2 anderen");
    w.ed.onServerEvent({ type: "office.presence", data: { node_id: "n1", count: 2 } });
    expect(statusText(w.ed.getState())).toBe("Bewerken met 1 ander");
    w.ed.onServerEvent({ type: "office.presence", data: { node_id: "ander", count: 9 } });
    expect(statusText(w.ed.getState())).toBe("Bewerken met 1 ander");
  });

  it("node.changed voor dit bestand met nieuwe inhoud: Opgeslagen om …", async () => {
    const w = world();
    await ready(w);
    saved(w);
    w.ed.onServerEvent({ type: "node.changed", data: { id: "ander", parent_id: "p" } });
    await flush();
    expect(w.ed.getState().savedAt).toBeNull();
    w.ed.onServerEvent({ type: "node.changed", data: { id: "n1", parent_id: "p" } });
    await flush();
    expect(w.ed.getState().savedAt).toBe(w.clock.now);
    expect(statusText(w.ed.getState())).toMatch(/^Bewerken · Opgeslagen om \d{1,2}:\d{2}$/);
    await w.clock.advance(60_000);
    saved(w);
    w.ed.onServerEvent({ type: "node.changed", data: { ids: ["x", "n1"], parent_id: "p" } });
    await flush();
    expect(w.ed.getState().savedAt).toBe(w.clock.now);
  });

  it("node.changed zonder nieuwe inhoud (hernoemen, verplaatsen, een label): geen Opgeslagen, en de melding blijft", async () => {
    const w = world();
    await ready(w);
    w.ed.onServerEvent({ type: "office.unsaved", data: { node_id: "n1", name: "x", reason: "no_room", final: false } });
    await w.ed.save();
    w.ed.onServerEvent({ type: "node.changed", data: { id: "n1", parent_id: "p" } });
    await flush();
    expect(w.ed.getState().savedAt).toBeNull();
    expect(w.ed.getState().saving).toBe(true);
    expect(w.ed.getState().notices.map((n) => n.kind)).toEqual(["unsaved"]);
    // Kan het bestand niet gelezen worden: ook niets beweren
    vi.mocked(w.deps.modifiedAt).mockRejectedValueOnce(new ApiError(503, "weg", "unavailable"));
    w.ed.onServerEvent({ type: "node.changed", data: { id: "n1", parent_id: "p" } });
    await flush();
    expect(w.ed.getState().savedAt).toBeNull();
    saved(w);
    w.ed.onServerEvent({ type: "node.changed", data: { id: "n1", parent_id: "p" } });
    await flush();
    expect(w.ed.getState()).toMatchObject({ saving: false, savedAt: w.clock.now, notices: [] });
  });

  it("Nu opslaan: Opslaan… tot het bestand verandert, of hooguit 10 s", async () => {
    const w = world();
    await ready(w);
    await w.ed.save();
    expect(w.deps.saveNow).toHaveBeenCalledTimes(1);
    expect(statusText(w.ed.getState())).toBe("Opslaan…");
    saved(w);
    w.ed.onServerEvent({ type: "node.changed", data: { id: "n1" } });
    await flush();
    expect(w.ed.getState().saving).toBe(false);
    await w.ed.save();
    await w.clock.advance(SAVING_MS);
    expect(w.ed.getState().saving).toBe(false); // niets te bewaren: geen node.changed
  });

  it("Nu opslaan mislukt: een melding, en niet meer Opslaan…", async () => {
    const w = world();
    await ready(w);
    vi.mocked(w.deps.saveNow).mockRejectedValueOnce(new ApiError(503, "De editor is nu niet bereikbaar", "office_unavailable"));
    await w.ed.save();
    expect(w.ed.getState().saving).toBe(false);
    expect(w.ed.getState().notices).toEqual([{ kind: "error", text: "De editor is nu niet bereikbaar", reload: false }]);
  });

  it("Nu opslaan vóór de editor klaar is: niets", async () => {
    const w = world();
    await w.ed.start();
    await w.ed.save();
    expect(w.deps.saveNow).not.toHaveBeenCalled();
  });

  it("waarschuwing -18: Verbinding weg met Opnieuw laden; weer wijzigingen: weer online", async () => {
    const w = world();
    const ev = await ready(w);
    ev.onWarning({ data: { warningCode: -5 } });
    expect(w.ed.getState().connection).toBe("online");
    ev.onWarning({ data: { warningCode: -18, warningDescription: "weg" } });
    expect(statusText(w.ed.getState())).toBe("Verbinding weg");
    expect(w.ed.getState().notices).toEqual([{ kind: "offline", text: expect.stringContaining("verbinding met de editor is weg"), reload: true }]);
    ev.onDocumentStateChange({ data: true });
    expect(w.ed.getState().connection).toBe("online");
    expect(w.ed.getState().notices).toEqual([]);
  });

  it("onError: een melding met de code", async () => {
    const w = world();
    const ev = await ready(w);
    ev.onError({ data: { errorCode: -4, errorDescription: "download" } });
    expect(w.ed.getState().notices).toEqual([{ kind: "error", text: expect.stringContaining("code -4"), reload: true }]);
  });

  it("office.unsaved met final false: een melding in de pagina; final true doet notices.ts", async () => {
    const w = world();
    await ready(w);
    w.ed.onServerEvent({ type: "office.unsaved", data: { node_id: "n1", name: "x", reason: "no_room", final: true } });
    expect(w.ed.getState().notices).toEqual([]);
    w.ed.onServerEvent({ type: "office.unsaved", data: { node_id: "ander", name: "x", reason: "no_room", final: false } });
    expect(w.ed.getState().notices).toEqual([]);
    w.ed.onServerEvent({ type: "office.unsaved", data: { node_id: "n1", name: "x", reason: "no_room", final: false } });
    expect(w.ed.getState().notices).toEqual([{ kind: "unsaved", text: expect.stringContaining("quotum van de eigenaar is vol"), reload: false }]);
    // Daarna wel opgeslagen: de melding weg
    saved(w);
    w.ed.onServerEvent({ type: "node.changed", data: { id: "n1" } });
    await flush();
    expect(w.ed.getState().notices).toEqual([]);
  });
});

describe("opnieuw en weg", () => {
  it("onRequestRefreshFile: een nieuwe sessie en refreshFile(config)", async () => {
    let n = 0;
    const w = world(async () => session(`k${++n}`));
    const ev = await ready(w);
    ev.onRequestRefreshFile();
    await flush();
    expect(w.open).toHaveBeenCalledTimes(2);
    expect(w.editors[0].refreshFile).toHaveBeenCalledWith(session("k2").config);
    expect(w.ed.getState().key).toBe("k2");
    expect(w.deps.reload).not.toHaveBeenCalled();
    expect(w.deps.mount).toHaveBeenCalledTimes(1);
  });

  it("lukt de nieuwe sessie niet, dan de pagina opnieuw laden", async () => {
    let n = 0;
    const w = world(async () => {
      n += 1;
      if (n > 1) throw new ApiError(503, "weg", "office_unavailable");
      return session();
    });
    const ev = await ready(w);
    ev.onRequestRefreshFile();
    await flush();
    expect(w.deps.reload).toHaveBeenCalledTimes(1);
  });

  it("zonder refreshFile in de editor: de pagina opnieuw laden", async () => {
    const w = world();
    const ev = await ready(w);
    delete (w.editors[0] as Partial<(typeof w.editors)[0]>).refreshFile;
    ev.onRequestRefreshFile();
    await flush();
    expect(w.deps.reload).toHaveBeenCalledTimes(1);
  });

  it("office.dropped stale met deze key: stil een nieuwe sessie; een andere key: niets", async () => {
    let n = 0;
    const w = world(async () => session(`k${++n}`));
    await ready(w);
    w.ed.onServerEvent({ type: "office.dropped", data: { node_id: "n1", key: "oud", reason: "stale" } });
    await flush();
    expect(w.open).toHaveBeenCalledTimes(1);
    w.ed.onServerEvent({ type: "office.dropped", data: { node_id: "n1", key: "k1", reason: "stale" } });
    await flush();
    expect(w.open).toHaveBeenCalledTimes(2);
    expect(w.editors[0].refreshFile).toHaveBeenCalledTimes(1);
    expect(w.ed.getState().notices).toEqual([]);
  });

  it("office.dropped access: de editor dicht, met een melding", async () => {
    const w = world();
    await ready(w);
    w.ed.onServerEvent({ type: "office.dropped", data: { node_id: "ander", key: "k1", reason: "access" } });
    expect(w.ed.getState().phase).toBe("ready");
    w.ed.onServerEvent({ type: "office.dropped", data: { node_id: "n1", key: "k1", reason: "access" } });
    expect(w.ed.getState().phase).toBe("gone");
    expect(w.editors[0].destroyEditor).toHaveBeenCalledTimes(1);
    expect(w.stopGate).toHaveBeenCalledTimes(1);
    expect(w.ed.getState().notices).toEqual([{ kind: "dropped", text: "Je hebt geen toegang meer; de editor is gesloten.", reload: false }]);
    expect(statusText(w.ed.getState())).toBe("De editor is gesloten");
  });

  it("Sluiten en onRequestClose: de editor weg en terug", async () => {
    const w = world();
    const ev = await ready(w);
    ev.onRequestClose();
    expect(w.editors[0].destroyEditor).toHaveBeenCalledTimes(1);
    expect(w.deps.close).toHaveBeenCalledTimes(1);
    w.ed.close();
    expect(w.editors[0].destroyEditor).toHaveBeenCalledTimes(1);
    expect(w.deps.close).toHaveBeenCalledTimes(2);
  });

  it("de pagina verlaten tijdens het openen: geen editor, geen cookie", async () => {
    let release: (s: SessionOut) => void = () => {};
    const w = world(() => new Promise((r) => (release = r)));
    const started = w.ed.start();
    w.ed.destroy();
    release(session());
    await started;
    expect(w.deps.mount).not.toHaveBeenCalled();
    expect(w.deps.keepGate).not.toHaveBeenCalled();
  });

  it("verlaten tijdens het wachten op office_busy: niet meer vragen", async () => {
    const w = world(async () => {
      throw busy();
    });
    const started = w.ed.start();
    await flush();
    w.ed.destroy();
    await w.clock.advance(BUSY_MAX_MS);
    await started;
    expect(w.open).toHaveBeenCalledTimes(1);
  });

  it("na destroy doen gebeurtenissen niets meer, en er loopt geen klok", async () => {
    const w = world();
    await ready(w);
    w.ed.destroy();
    w.ed.onServerEvent({ type: "office.presence", data: { node_id: "n1", count: 4 } });
    expect(w.ed.getState().others).toBe(0);
    expect(w.clock.pending()).toBe(0);
  });
});

describe("hulpjes", () => {
  it("statusText vóór en na", () => {
    expect(statusText(initialState())).toBe("Verbinden…");
    expect(statusText({ ...initialState(), phase: "ready", connection: "online", others: 0 })).toBe("Bewerken");
    expect(statusText({ ...initialState(), phase: "gone" })).toBe("De editor is gesloten");
  });

  it("Alt+Shift+Q, ook als de toets een ander teken geeft (Mac)", () => {
    const key = (k: Partial<KeyboardEvent>) => ({ key: "", code: "", altKey: false, shiftKey: false, ctrlKey: false, metaKey: false, ...k });
    expect(isLeaveKey(key({ key: "Q", code: "KeyQ", altKey: true, shiftKey: true }), false)).toBe(true);
    expect(isLeaveKey(key({ key: "Œ", code: "KeyQ", altKey: true, shiftKey: true }), true)).toBe(true);
    expect(isLeaveKey(key({ key: "Q", code: "KeyQ", shiftKey: true }), false)).toBe(false);
    expect(isLeaveKey(key({ key: "q", code: "KeyQ", altKey: true }), false)).toBe(false);
    expect(isLeaveKey(key({ key: "Q", code: "KeyQ", altKey: true, shiftKey: true, ctrlKey: true }), false)).toBe(false);
  });

  it("sessionBody: thema en smal scherm", () => {
    expect(sessionBody("light", true, false)).toEqual({ type: "desktop", theme: "light" });
    expect(sessionBody("dark", false, true)).toEqual({ type: "mobile", theme: "dark" });
    expect(sessionBody("system", true, false)).toEqual({ type: "desktop", theme: "dark" });
    expect(sessionBody("system", false, false)).toEqual({ type: "desktop", theme: "light" });
  });
});

describe("api.js laden", () => {
  beforeEach(() => resetApiForTests());

  function host(): ScriptHost & { injected: string[]; finish(ok: boolean, api?: DocsApi): void; removed: number } {
    const done: ((ok: boolean) => void)[] = [];
    let current: DocsApi | undefined;
    const h = {
      injected: [] as string[],
      removed: 0,
      inject(src: string, cb: (ok: boolean) => void) {
        h.injected.push(src);
        done.push(cb);
        return () => {
          h.removed += 1;
        };
      },
      docsApi: () => current,
      finish(ok: boolean, api?: DocsApi) {
        current = api;
        done.shift()?.(ok);
      },
    };
    return h;
  }

  const docs = { DocEditor: class {} } as unknown as DocsApi;

  it("één script, ook bij twee aanroepen tegelijk", async () => {
    const h = host();
    const a = loadApi("/office/api.js", h);
    const b = loadApi("/office/api.js", h);
    h.finish(true, docs);
    expect(await a).toBe(docs);
    expect(await b).toBe(docs);
    expect(h.injected).toEqual(["/office/api.js"]);
    expect(await loadApi("/office/api.js", h)).toBe(docs);
    expect(h.injected).toHaveLength(1);
  });

  it("mislukt: het element weg, en de volgende keer opnieuw", async () => {
    const h = host();
    const a = loadApi("/office/api.js", h);
    h.finish(false);
    await expect(a).rejects.toThrow();
    expect(h.removed).toBe(1);
    const b = loadApi("/office/api.js", h);
    h.finish(true, docs);
    expect(await b).toBe(docs);
    expect(h.injected).toHaveLength(2);
  });

  it("geladen maar zonder DocsAPI: ook een fout", async () => {
    const h = host();
    const a = loadApi("/office/api.js", h);
    h.finish(true, undefined);
    await expect(a).rejects.toThrow(/DocsAPI/);
  });
});

describe("het poort-cookie", () => {
  it("elke 30 minuten vernieuwen, een fout stoort niet, en stoppen stopt", async () => {
    const clock = new Clock();
    const renew = vi.fn().mockRejectedValueOnce(new ApiError(404, "weg", "not_found")).mockResolvedValue(undefined);
    let handle: unknown = null;
    const repeat: Timers = {
      set(fn, ms) {
        const tick = () => {
          fn();
          handle = clock.set(tick, ms);
        };
        handle = clock.set(tick, ms);
        return "interval";
      },
      clear() {
        clock.clear(handle);
      },
    };
    const stop = keepGate(renew, GATE_RENEW_MS, repeat);
    await clock.advance(GATE_RENEW_MS - 1);
    expect(renew).not.toHaveBeenCalled();
    await clock.advance(1);
    expect(renew).toHaveBeenCalledTimes(1);
    await clock.advance(GATE_RENEW_MS);
    expect(renew).toHaveBeenCalledTimes(2);
    stop();
    await clock.advance(GATE_RENEW_MS * 3);
    expect(renew).toHaveBeenCalledTimes(2);
  });
});
