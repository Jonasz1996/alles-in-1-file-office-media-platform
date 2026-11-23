// De breedte van de viewer (0.8.2, docs/fase-6/z4.md Open): base.css houdt altijd ruimte voor de
// schuifbalk (scrollbar-gutter: stable). Een viewer van 100vw lag in een browser die die ruimte
// niet van 100vw aftrekt met zijn laatste pixels onder de schuifbalk. Een modale <dialog> is
// position: fixed, dus 100% is het venster zonder de schuifbalk.

import { describe, expect, it } from "vitest";
import base from "../base.css?raw";
import viewer from "./viewer.css?raw";

/** De declaraties van de eerste regel met precies deze selector (zonder commentaar). */
function rule(css: string, selector: string): string {
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const start = plain.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  return plain.slice(start, plain.indexOf("}", start));
}

describe("de viewer vult het venster zonder de schuifbalk", () => {
  it("100% breed, nooit 100vw", () => {
    const own = rule(viewer, ".viewer");
    expect(own).toMatch(/\bwidth: 100%;/);
    expect(own).not.toMatch(/vw\b/);
    expect(own).toMatch(/\bmax-width: none;/);
  });

  it("base.css houdt de ruimte voor de schuifbalk vrij (daarom 100% en niet 100vw)", () => {
    expect(rule(base, ":root")).toMatch(/scrollbar-gutter: stable;/);
  });
});
