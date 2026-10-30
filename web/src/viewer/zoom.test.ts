import { describe, expect, it } from "vitest";
import { arrowPan, clampPan, fitScale, fitted, isZoomed, MAX_SCALE, neededWidth, rotate, zoomAt, zoomTo } from "./zoom";

const natural = { w: 4000, h: 3000 };
const viewport = { w: 1000, h: 800 };

describe("passend", () => {
  it("past in het venster en vergroot nooit", () => {
    expect(fitScale(natural, viewport)).toBe(0.25);
    expect(fitScale({ w: 200, h: 100 }, viewport)).toBe(1);
    // Gedraaid: de hoogte telt als breedte
    expect(fitScale(natural, viewport, 90)).toBeCloseTo(800 / 4000);
  });
});

describe("zoomen", () => {
  it("rond een punt: wat onder de muis staat, blijft staan", () => {
    const start = fitted(natural, viewport);
    const point = { x: 200, y: -100 };
    const next = zoomAt(start, 2, point, natural, viewport);
    expect(next.scale).toBe(0.5);
    // Het beeldpunt onder de muis: (point - pan) / scale, voor en na gelijk
    const before = { x: (point.x - start.x) / start.scale, y: (point.y - start.y) / start.scale };
    const after = { x: (point.x - next.x) / next.scale, y: (point.y - next.y) / next.scale };
    expect(after.x).toBeCloseTo(before.x);
    expect(after.y).toBeCloseTo(before.y);
  });

  it("tussen passend en het maximum", () => {
    const start = fitted(natural, viewport);
    expect(zoomAt(start, 0.1, { x: 0, y: 0 }, natural, viewport).scale).toBe(0.25);
    expect(zoomAt(start, 1000, { x: 0, y: 0 }, natural, viewport).scale).toBe(MAX_SCALE);
    expect(zoomTo(start, 1, natural, viewport).scale).toBe(1);
  });

  it("verschuiven tot de rand, niet verder", () => {
    const big = zoomTo(fitted(natural, viewport), 1, natural, viewport);
    const clamped = clampPan({ ...big, x: 99_999, y: -99_999 }, natural, viewport);
    expect(clamped.x).toBe((4000 - 1000) / 2);
    expect(clamped.y).toBe(-(3000 - 800) / 2);
    // Past hij, dan blijft hij in het midden
    expect(clampPan({ ...fitted(natural, viewport), x: 50 }, natural, viewport).x).toBe(0);
  });
});

describe("pijltjes", () => {
  it("pannen alleen als er is ingezoomd (anders navigeren ze)", () => {
    const fit = fitted(natural, viewport);
    expect(isZoomed(fit, natural, viewport)).toBe(false);
    expect(arrowPan(fit, "right", natural, viewport)).toBeNull();
    const zoomed = zoomTo(fit, 1, natural, viewport);
    expect(isZoomed(zoomed, natural, viewport)).toBe(true);
    // → laat zien wat rechts staat: de afbeelding schuift 10 % van het venster naar links
    expect(arrowPan(zoomed, "right", natural, viewport)).toMatchObject({ x: -100, y: 0 });
    expect(arrowPan(zoomed, "left", natural, viewport)).toMatchObject({ x: 100 });
    expect(arrowPan(zoomed, "down", natural, viewport)).toMatchObject({ y: -80 });
    expect(arrowPan(zoomed, "up", natural, viewport)).toMatchObject({ y: 80 });
  });

  it("aan de rand houdt het op", () => {
    let state = zoomTo(fitted(natural, viewport), 1, natural, viewport);
    for (let i = 0; i < 100; i++) state = arrowPan(state, "right", natural, viewport)!;
    expect(state.x).toBe(-(4000 - 1000) / 2);
  });

  it("een kleine afbeelding (passend = 100 %) is pas ingezoomd boven 100 %", () => {
    const small = { w: 300, h: 200 };
    const fit = fitted(small, viewport);
    expect(arrowPan(fit, "left", small, viewport)).toBeNull();
    const twice = zoomTo(fit, 2, small, viewport);
    expect(isZoomed(twice, small, viewport)).toBe(true);
  });
});

describe("draaien en pixels", () => {
  it("een kwartslag, en dan weer passend", () => {
    const zoomed = zoomTo(fitted(natural, viewport), 1, natural, viewport);
    const turned = rotate(zoomed, natural, viewport);
    expect(turned.rotation).toBe(90);
    expect(turned.scale).toBeCloseTo(0.2);
    expect(rotate({ ...turned, rotation: 270 }, natural, viewport).rotation).toBe(0);
  });

  it("hoeveel pixels nodig zijn (met de pixeldichtheid)", () => {
    const fit = fitted(natural, viewport);
    expect(neededWidth(fit, natural, 1)).toBe(1000);
    expect(neededWidth(fit, natural, 2)).toBe(2000);
  });
});
