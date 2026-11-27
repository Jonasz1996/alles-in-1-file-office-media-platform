import { describe, expect, it } from "vitest";
import { canvasSize, containRect, MAX_PIXELS, worth } from "./upscale";

describe("upscaling: maten", () => {
  it("contain zoals de video", () => {
    expect(containRect(1920, 1080, 3840, 2160)).toEqual({ x: 0, y: 0, w: 3840, h: 2160 });
    expect(containRect(1920, 800, 1920, 1080)).toEqual({ x: 0, y: 140, w: 1920, h: 800 });
    expect(containRect(640, 480, 1920, 1080)).toEqual({ x: 240, y: 0, w: 1440, h: 1080 });
    expect(containRect(0, 0, 100, 100)).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });

  it("canvas op echte pixels, hooguit 4K", () => {
    expect(canvasSize(1280, 720, 1.5)).toEqual({ w: 1920, h: 1080 });
    const big = canvasSize(3840, 2160, 2);
    expect(big.w * big.h).toBeLessThanOrEqual(MAX_PIXELS);
    expect(big.w / big.h).toBeCloseTo(16 / 9, 2);
  });

  it("alleen als het beeld echt groter getoond wordt", () => {
    expect(worth(1280, 720, 1920, 1080)).toBe(true);
    expect(worth(1920, 1080, 1920, 1080)).toBe(false);
    expect(worth(1920, 1080, 2000, 1125)).toBe(false);
    expect(worth(0, 0, 1920, 1080)).toBe(false);
  });
});
