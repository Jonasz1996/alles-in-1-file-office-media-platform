import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isAbort, LatestOnly, SETTLE_MS } from "./loader";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

function setup() {
  const started: { key: string; signal: AbortSignal; resolve(v: string): void; reject(e: unknown): void }[] = [];
  const done: [string, string][] = [];
  const failed: [string, unknown][] = [];
  const busy: boolean[] = [];
  const loader = new LatestOnly<string>({
    delay: SETTLE_MS,
    run: (key, signal) =>
      new Promise<string>((resolve, reject) => {
        started.push({ key, signal, resolve, reject });
        signal.addEventListener("abort", () => reject(new DOMException("weg", "AbortError")));
      }),
    done: (key, value) => done.push([key, value]),
    failed: (key, e) => failed.push([key, e]),
    busy: (b) => busy.push(b),
  });
  return { loader, started, done, failed, busy };
}

describe("laden terwijl je schuift: alleen het laatste verzoek telt", () => {
  it("een reeks vragen tijdens het slepen geeft één verzoek, als het venster stilstaat", async () => {
    const { loader, started, done } = setup();
    for (let i = 0; i < 20; i++) {
      loader.request(`p${i}`);
      vi.advanceTimersByTime(SETTLE_MS - 10);
    }
    expect(started).toHaveLength(0);
    vi.advanceTimersByTime(10);
    expect(started.map((s) => s.key)).toEqual(["p19"]);
    started[0].resolve("cellen");
    await vi.runAllTimersAsync();
    expect(done).toEqual([["p19", "cellen"]]);
  });

  it("een nieuwe vraag breekt het lopende verzoek af; het oude antwoord komt nooit", async () => {
    const { loader, started, done, failed } = setup();
    loader.request("a", 0);
    expect(started.map((s) => s.key)).toEqual(["a"]);
    loader.request("b");
    expect(started[0].signal.aborted).toBe(true);
    started[0].resolve("te laat");
    vi.advanceTimersByTime(SETTLE_MS);
    expect(started.map((s) => s.key)).toEqual(["a", "b"]);
    started[1].resolve("goed");
    await vi.runAllTimersAsync();
    expect(done).toEqual([["b", "goed"]]);
    expect(failed).toEqual([]);
  });

  it("dezelfde vraag nog eens doet niets; na een fout mag hij weer", async () => {
    const { loader, started, failed } = setup();
    loader.request("a", 0);
    loader.request("a", 0);
    expect(started).toHaveLength(1);
    const boom = new Error("500");
    started[0].reject(boom);
    await vi.runAllTimersAsync();
    expect(failed).toEqual([["a", boom]]);
    loader.request("a", 0);
    expect(started).toHaveLength(2);
  });

  it("cancel breekt af en vergeet: daarna gaat dezelfde vraag wel weer", async () => {
    const { loader, started, done } = setup();
    loader.request("a", 0);
    loader.cancel();
    expect(started[0].signal.aborted).toBe(true);
    loader.request("a", 0);
    expect(started).toHaveLength(2);
    started[1].resolve("ok");
    await vi.runAllTimersAsync();
    expect(done).toEqual([["a", "ok"]]);
    // forget na een antwoord: dezelfde vraag gaat opnieuw
    loader.forget();
    loader.request("a", 0);
    expect(started).toHaveLength(3);
  });

  it("bezig van de vraag tot het antwoord of de fout; afbreken en een vervanger tellen mee", async () => {
    const { loader, started, busy } = setup();
    expect(loader.busy).toBe(false);
    loader.request("a");
    expect(loader.busy).toBe(true); // wacht nog op het stilstaan
    vi.advanceTimersByTime(SETTLE_MS);
    loader.request("b"); // vervangt a: blijft bezig, zonder tussendoor "klaar"
    vi.advanceTimersByTime(SETTLE_MS);
    expect(busy).toEqual([true]);
    started[1].resolve("ok");
    await vi.runAllTimersAsync();
    expect(loader.busy).toBe(false);
    expect(busy).toEqual([true, false]);
    // Dezelfde vraag als het laatste antwoord: niets te doen, en dus niet bezig
    loader.request("b");
    expect(loader.busy).toBe(false);
    // Een fout maakt hem ook klaar, en cancel ook
    loader.request("c", 0);
    started[2].reject(new Error("500"));
    await vi.runAllTimersAsync();
    loader.request("d");
    loader.cancel();
    expect(busy).toEqual([true, false, true, false, true, false]);
  });

  it("vraagt done zelf meteen iets nieuws, dan blijft hij bezig", async () => {
    const asked: string[] = [];
    const busy: boolean[] = [];
    let resolveFirst: (v: string) => void = () => {};
    const loader: LatestOnly<string> = new LatestOnly<string>({
      delay: SETTLE_MS,
      run: (key) =>
        new Promise<string>((resolve) => {
          asked.push(key);
          if (key === "ruim") resolveFirst = resolve;
        }),
      done: (key) => {
        if (key === "ruim") loader.request("krap", 0);
      },
      failed: () => {},
      busy: (b) => busy.push(b),
    });
    loader.request("ruim", 0);
    resolveFirst("te veel");
    await vi.runAllTimersAsync();
    expect(asked).toEqual(["ruim", "krap"]);
    expect(loader.busy).toBe(true);
    expect(busy).toEqual([true]);
  });

  it("afbreken herkent hij aan de naam AbortError", () => {
    expect(isAbort(new DOMException("x", "AbortError"))).toBe(true);
    expect(isAbort({ name: "AbortError" })).toBe(true);
    expect(isAbort(new Error("x"))).toBe(false);
    expect(isAbort(null)).toBe(false);
  });
});
