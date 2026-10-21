import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { announcer, defaultTimeout, LiveAnnouncer, ToastStore, toast, toasts } from "./toast";
import { LayerStack } from "./topLayer";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("defaultTimeout", () => {
  it("5 s; een fout of een actie (Ongedaan maken) 8 s; een eigen waarde wint", () => {
    expect(defaultTimeout({})).toBe(5000);
    expect(defaultTimeout({ kind: "ok" })).toBe(5000);
    expect(defaultTimeout({ kind: "error" })).toBe(8000);
    expect(defaultTimeout({ action: { label: "Ongedaan maken", run() {} } })).toBe(8000);
    expect(defaultTimeout({ kind: "error", timeoutMs: 0 })).toBe(0);
    expect(defaultTimeout({ timeoutMs: 1234 })).toBe(1234);
  });
});

describe("ToastStore", () => {
  it("verdwijnt vanzelf na de timeout", () => {
    const store = new ToastStore();
    store.show("Opgeslagen");
    expect(store.getSnapshot().map((t) => t.message)).toEqual(["Opgeslagen"]);
    vi.advanceTimersByTime(4999);
    expect(store.getSnapshot()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(store.getSnapshot()).toHaveLength(0);
  });

  it("timeout 0 blijft staan tot hij gesloten wordt", () => {
    const store = new ToastStore();
    const close = store.show("Bezig…", { timeoutMs: 0 });
    vi.advanceTimersByTime(60_000);
    expect(store.getSnapshot()).toHaveLength(1);
    close();
    expect(store.getSnapshot()).toHaveLength(0);
  });

  it("hetzelfde id vervangt de vorige, met een nieuwe timer", () => {
    const store = new ToastStore();
    store.show("1 van 3", { id: "upload" });
    vi.advanceTimersByTime(4000);
    store.show("2 van 3", { id: "upload" });
    vi.advanceTimersByTime(4000);
    expect(store.getSnapshot().map((t) => t.message)).toEqual(["2 van 3"]);
    vi.advanceTimersByTime(1000);
    expect(store.getSnapshot()).toHaveLength(0);
  });

  it("hooguit vier tegelijk: de oudste gaan weg", () => {
    const store = new ToastStore();
    for (let i = 1; i <= 6; i++) store.show(`melding ${i}`);
    expect(store.getSnapshot().map((t) => t.message)).toEqual(["melding 3", "melding 4", "melding 5", "melding 6"]);
  });

  it("de actie draait één keer en sluit de toast", () => {
    const store = new ToastStore();
    const run = vi.fn();
    store.show("Naar de prullenbak", { action: { label: "Ongedaan maken", run } });
    const id = store.getSnapshot()[0].id;
    store.runAction(id);
    store.runAction(id);
    expect(run).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toHaveLength(0);
  });

  it("pauze zolang de muis erop staat, daarna weer de hele tijd", () => {
    const store = new ToastStore();
    store.show("Let op", { kind: "error" });
    const id = store.getSnapshot()[0].id;
    vi.advanceTimersByTime(7000);
    store.pause(id);
    vi.advanceTimersByTime(60_000);
    expect(store.getSnapshot()).toHaveLength(1);
    store.resume(id);
    vi.advanceTimersByTime(7999);
    expect(store.getSnapshot()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(store.getSnapshot()).toHaveLength(0);
  });

  it("meldt wijzigingen; sluiten van iets dat er niet is meldt niets", () => {
    const store = new ToastStore();
    const l = vi.fn();
    store.subscribe(l);
    const close = store.show("Hoi");
    close();
    close();
    store.dismiss("bestaat-niet");
    expect(l).toHaveBeenCalledTimes(2);
  });
});

describe("LiveAnnouncer", () => {
  it("polite en assertive apart", () => {
    const a = new LiveAnnouncer();
    a.announce("Map geopend");
    a.announce("Mislukt", "assertive");
    expect(a.getSnapshot()).toEqual({ polite: "Map geopend", assertive: "Mislukt" });
  });

  it("dezelfde tekst na elkaar verandert toch (anders leest de schermlezer hem niet opnieuw)", () => {
    const a = new LiveAnnouncer();
    const l = vi.fn();
    a.subscribe(l);
    a.announce("3 geselecteerd");
    const first = a.getSnapshot().polite;
    a.announce("3 geselecteerd");
    const second = a.getSnapshot().polite;
    a.announce("3 geselecteerd");
    expect(second).not.toBe(first);
    expect(second.trim()).toBe("3 geselecteerd");
    expect(a.getSnapshot().polite).not.toBe(second);
    expect(l).toHaveBeenCalledTimes(3);
  });
});

describe("toast() en de live-regio", () => {
  it("elke toast gaat ook via de vaste live-regio; een fout direct", () => {
    const close = toast("Opgeslagen");
    expect(announcer.getSnapshot().polite).toBe("Opgeslagen");
    const fail = toast("Mislukt", { kind: "error" });
    expect(announcer.getSnapshot().assertive).toBe("Mislukt");
    expect(announcer.lastAt).toBeGreaterThan(0);
    close();
    fail();
    expect(toasts.getSnapshot()).toHaveLength(0);
  });
});

describe("LayerStack", () => {
  it("de bovenste open dialoog; sluiten in elke volgorde", () => {
    const s = new LayerStack<string>();
    const changed = vi.fn();
    s.subscribe(changed);
    expect(s.top()).toBeNull();
    const a = s.open("a");
    const b = s.open("b");
    expect(s.top()).toBe("b");
    a();
    expect(s.top()).toBe("b");
    b();
    b(); // twee keer sluiten doet niets extra
    expect(s.top()).toBeNull();
    expect(changed).toHaveBeenCalledTimes(4);
  });
});
