import { describe, expect, it } from "vitest";
import { clampOffset, offsetText, savedOffset, shiftCues } from "./subshift";

describe("ondertitel verschuiven", () => {
  it("rekent altijd vanaf het origineel", () => {
    const cues = [
      { startTime: 1, endTime: 2 },
      { startTime: 10, endTime: 12 },
    ];
    expect(shiftCues(cues, 1500)).toBe(2);
    expect(cues[0]).toEqual({ startTime: 2.5, endTime: 3.5 });
    shiftCues(cues, -500);
    expect(cues[0]).toEqual({ startTime: 0.5, endTime: 1.5 });
    expect(cues[1]).toEqual({ startTime: 9.5, endTime: 11.5 });
    shiftCues(cues, -5000); // niet onder nul
    expect(cues[0]).toEqual({ startTime: 0, endTime: 0 });
    shiftCues(cues, 0);
    expect(cues[1]).toEqual({ startTime: 10, endTime: 12 });
    expect(shiftCues(null, 100)).toBe(0);
  });

  it("grens, tekst en bewaarde waarde", () => {
    expect(clampOffset(20 * 60 * 1000)).toBe(10 * 60 * 1000);
    expect(clampOffset(-0.4)).toBe(-0);
    expect(offsetText(1500, "nl-NL")).toBe("+1,5 s");
    expect(offsetText(-300, "en-GB")).toBe("−0.3 s");
    const saved = [{ sub_id: "x0", label: "Nederlands", offset_ms: 800 }];
    expect(savedOffset(saved, "x0", "Nederlands")).toBe(800);
    expect(savedOffset(saved, "x0", "Engels")).toBe(0);
    expect(savedOffset(saved, "e2", "Nederlands")).toBe(0);
  });
});
