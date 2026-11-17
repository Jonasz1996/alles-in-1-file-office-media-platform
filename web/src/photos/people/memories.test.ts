import { describe, expect, it } from "vitest";
import memories from "../Memories.tsx?raw";

// De herinneringen staan boven de tijdlijn. De scroll-meting van e2e/perf (fase 6, Z6) vond met
// elf jaren van twaalf foto's lange taken tijdens het scrollen (9 bij CPU 4×, zonder het blok 0):
// 132 thumbnails tegelijk, met afgeronde hoeken en overflow: hidden. Deze test houdt de oplossing
// in Memories.tsx vast. De css (content-visibility per jaar, een vaste maat per foto) controleert
// e2e/test_zoek_personen.py in de browser, want vitest geeft een css-bestand leeg terug; de
// meting zelf is e2e/perf/scroll.py.

describe("herinneringen maken de tijdlijn niet trager", () => {
  it("de thumbnails komen via de wachtrij van het raster, een paar per frame", () => {
    expect(memories).toContain('import { requestImage } from "./Grid";');
    expect(memories).toContain("requestImage(() => setReady(true))");
    expect(memories).toMatch(/\{ready && it\.thumb && <img /);
  });
});
