import { describe, expect, it } from "vitest";
import { keepFresh, renewDelay, type Timers } from "./renew";

describe("renewDelay", () => {
  it("een minuut voor het verloopt, bij een kort token halverwege, nooit binnen 5 s", () => {
    expect(renewDelay(600)).toBe(540_000);
    expect(renewDelay(121)).toBe(61_000);
    expect(renewDelay(120)).toBe(60_000);
    expect(renewDelay(60)).toBe(30_000);
    expect(renewDelay(4)).toBe(5_000);
    expect(renewDelay(0)).toBe(5_000);
    expect(renewDelay(-10)).toBe(5_000);
  });
});

class FakeTimers implements Timers {
  now = 0;
  private queue: { at: number; fn: () => void; id: number }[] = [];
  private next = 0;

  set(fn: () => void, ms: number): unknown {
    const id = ++this.next;
    this.queue.push({ at: this.now + ms, fn, id });
    return id;
  }

  clear(handle: unknown): void {
    this.queue = this.queue.filter((t) => t.id !== handle);
  }

  /** Tijd laten lopen en wat dan aan de beurt is draaien (met de beloftes ertussen). */
  async advance(ms: number): Promise<void> {
    const end = this.now + ms;
    for (;;) {
      this.queue.sort((a, b) => a.at - b.at);
      const first = this.queue[0];
      if (!first || first.at > end) break;
      this.queue.shift();
      this.now = first.at;
      first.fn();
      for (let i = 0; i < 5; i++) await Promise.resolve();
    }
    this.now = end;
  }

  get pending(): number {
    return this.queue.length;
  }
}

describe("keepFresh", () => {
  it("vernieuwt steeds op tijd, met de nieuwe looptijd", async () => {
    const timers = new FakeTimers();
    const at: number[] = [];
    const stop = keepFresh(
      600,
      async () => {
        at.push(timers.now);
        return 600;
      },
      timers,
    );
    await timers.advance(539_000);
    expect(at).toEqual([]);
    await timers.advance(1_000);
    expect(at).toEqual([540_000]);
    await timers.advance(540_000);
    expect(at).toEqual([540_000, 1_080_000]);
    stop();
    expect(timers.pending).toBe(0);
    await timers.advance(10_000_000);
    expect(at).toHaveLength(2);
  });

  it("mislukt het, dan over een minuut opnieuw", async () => {
    const timers = new FakeTimers();
    const at: number[] = [];
    let fail = true;
    keepFresh(
      600,
      async () => {
        at.push(timers.now);
        if (fail) {
          fail = false;
          throw new Error("offline");
        }
        return 600;
      },
      timers,
    );
    await timers.advance(540_000);
    await timers.advance(60_000);
    expect(at).toEqual([540_000, 600_000]);
  });
});
