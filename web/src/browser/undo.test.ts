import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toasts } from "../ui/toast";
import { canUndo, clearUndo, offerUndo, undoLast } from "./undo";

const shown = () => toasts.getSnapshot().map((x) => x.message);

describe("ongedaan maken (Mod+Z)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    clearUndo();
    for (const x of toasts.getSnapshot()) toasts.dismiss(x.id);
    vi.useRealTimers();
  });

  it("werkt zolang de melding staat, ook als hij langer blijft omdat de muis erop staat", () => {
    const run = vi.fn();
    offerUndo("a.txt staat in de prullenbak", "Ongedaan maken", run);
    const [item] = toasts.getSnapshot();
    toasts.pause(item.id); // de muis of de focus op de melding
    vi.advanceTimersByTime(20_000);
    expect(canUndo()).toBe(true);
    toasts.resume(item.id);
    vi.advanceTimersByTime(7_000);
    expect(canUndo()).toBe(true);
    undoLast();
    expect(run).toHaveBeenCalledTimes(1);
    expect(shown()).toEqual([]); // de melding gaat mee weg
    expect(canUndo()).toBe(false);
    undoLast();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("niet meer als de melding weg is: verlopen, gesloten of verdrongen", () => {
    const run = vi.fn();
    offerUndo("weg", "Ongedaan maken", run);
    vi.advanceTimersByTime(8_000);
    expect(canUndo()).toBe(false);

    offerUndo("gesloten", "Ongedaan maken", run);
    toasts.dismiss(toasts.getSnapshot()[0].id); // ×
    expect(canUndo()).toBe(false);

    offerUndo("verdrongen", "Ongedaan maken", run);
    for (let i = 0; i < 4; i++) toasts.show(`nieuw ${i}`);
    expect(shown()).not.toContain("verdrongen");
    expect(canUndo()).toBe(false);
    undoLast();
    expect(run).not.toHaveBeenCalled();
  });

  it("de knop in de melding draait hem één keer; uitloggen haalt hem weg", () => {
    const run = vi.fn();
    offerUndo("knop", "Ongedaan maken", run);
    toasts.runAction(toasts.getSnapshot()[0].id);
    expect(run).toHaveBeenCalledTimes(1);
    expect(canUndo()).toBe(false);

    offerUndo("uitloggen", "Ongedaan maken", run);
    clearUndo();
    expect(canUndo()).toBe(false);
    expect(shown()).toEqual([]);
  });
});
