import { describe, expect, it } from "vitest";
import { latToY, lonToX, MAX_LAT, project, TILE, unproject, worldSize, xToLon, yToLat } from "./projection";

describe("Webmercator (Keuze 31)", () => {
  it("heen en terug geeft dezelfde plek, ook bij de randen", () => {
    for (const [lon, lat] of [
      [0, 0],
      [5.1214, 52.0907],
      [-74.006, 40.7128],
      [151.2093, -33.8688],
      [-179.999, -84.9],
      [179.999, 84.9],
    ]) {
      const [x, y] = project(lon, lat);
      const [lon2, lat2] = unproject(x, y);
      expect(lon2).toBeCloseTo(lon, 9);
      expect(lat2).toBeCloseTo(lat, 9);
    }
  });

  it("180° west is x = 0 en 180° oost is x = 1; terug weer ±180", () => {
    expect(lonToX(-180)).toBe(0);
    expect(lonToX(180)).toBe(1);
    expect(lonToX(0)).toBe(0.5);
    expect(xToLon(0)).toBe(-180);
    expect(xToLon(1)).toBe(180);
    // voorbij de datumgrens: de rand, niet een tweede wereld
    expect(lonToX(190)).toBe(1);
    expect(lonToX(-200)).toBe(0);
  });

  it("de evenaar is het midden; voorbij ±85,05° de rand van de kaart", () => {
    expect(latToY(0)).toBeCloseTo(0.5, 12);
    expect(latToY(MAX_LAT)).toBeCloseTo(0, 9);
    expect(latToY(-MAX_LAT)).toBeCloseTo(1, 9);
    expect(latToY(90)).toBe(latToY(MAX_LAT));
    expect(latToY(-90)).toBe(latToY(-MAX_LAT));
    expect(yToLat(0)).toBeCloseTo(MAX_LAT, 9);
    expect(yToLat(1)).toBeCloseTo(-MAX_LAT, 9);
    // het noorden staat boven
    expect(latToY(52)).toBeLessThan(latToY(10));
  });

  it("Utrecht ligt in de tegel van zoom 16 die photo_geo ook rekent", () => {
    // x16 = floor(x · 2^16), zoals app/search/bench.tegel16 (de formule van photo_geo): (33700, 21620)
    const [x, y] = project(5.1214, 52.0907);
    expect(Math.floor(x * 65536)).toBe(33700);
    expect(Math.floor(y * 65536)).toBe(21620);
  });

  it("de wereld is 256 pixels op zoom 0 en verdubbelt per stap", () => {
    expect(TILE).toBe(256);
    expect(worldSize(0)).toBe(256);
    expect(worldSize(3)).toBe(2048);
    expect(worldSize(14)).toBe(256 * 16384);
  });
});
