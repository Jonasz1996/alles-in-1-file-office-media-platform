import { describe, expect, it, vi } from "vitest";
import { clearMapFocus, onMapFocus, peekMapFocus, showOnMap } from "./focus";

describe("Op de kaart (alleen in het geheugen van de pagina, Keuze 27)", () => {
  it("bewaart de plek tot de kaart opent, en laat hem dan los", () => {
    showOnMap(52.0907, 5.1214);
    expect(peekMapFocus()).toEqual({ lat: 52.0907, lon: 5.1214 });
    expect(peekMapFocus()).toEqual({ lat: 52.0907, lon: 5.1214 });
    clearMapFocus();
    expect(peekMapFocus()).toBeNull();
  });

  it("geen plek bij een getal dat er geen is", () => {
    showOnMap(Number.NaN, 5);
    expect(peekMapFocus()).toBeNull();
    showOnMap(52, Number.POSITIVE_INFINITY);
    expect(peekMapFocus()).toBeNull();
  });

  it("een open kaart krijgt de plek meteen, en na het afmelden weer de volgende keer", () => {
    const seen = vi.fn();
    const stop = onMapFocus(seen);
    showOnMap(48.8566, 2.3522);
    expect(seen).toHaveBeenCalledWith({ lat: 48.8566, lon: 2.3522 });
    expect(peekMapFocus()).toBeNull();
    stop();
    showOnMap(1, 2);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(peekMapFocus()).toEqual({ lat: 1, lon: 2 });
    clearMapFocus();
  });

  it("een oude kaart meldt een nieuwe niet af", () => {
    const oud = vi.fn();
    const nieuw = vi.fn();
    const stopOud = onMapFocus(oud);
    const stopNieuw = onMapFocus(nieuw);
    stopOud();
    showOnMap(3, 4);
    expect(oud).not.toHaveBeenCalled();
    expect(nieuw).toHaveBeenCalledWith({ lat: 3, lon: 4 });
    stopNieuw();
  });
});
