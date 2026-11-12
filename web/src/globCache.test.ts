import { describe, expect, it } from "vitest";

// Vitest bewaart getransformeerde modules op schijf (fsModuleCache, standaard aan) onder een
// sleutel van de broncode. Een bestand dat import.meta.glob aanroept slaat hij alleen over als de
// bron letterlijk "import.meta.glob(" bevat. Met een typeargument tussen glob en "(" komt het
// bestand toch in de cache, met de lijst bestanden van toen: een nieuw woordenboek (zoals
// photos_faces en beeld van Z0b) ontbreekt dan in de tests tot de bron zelf verandert.
// Dus: nooit een typeargument of spatie na import.meta.glob, en het type met "as" erachter.
const sources = import.meta.glob(["./**/*.ts", "./**/*.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const CALL = /import\.meta\.glob\s*[<(]/;
const PLAIN = /import\.meta\.glob\(/g;
const WRONG = /import\.meta\.glob(\s+\(|\s*<)/;

describe("import.meta.glob zonder typeargument (de module-cache van vitest)", () => {
  // Vite laat dit bestand zelf uit de lijst
  it("leest de bronnen echt: de bekende aanroepen staan erin", () => {
    const callers = Object.keys(sources)
      .filter((p) => CALL.test(sources[p] ?? ""))
      .sort();
    expect(callers).toEqual([
      "./i18n/i18n.test.ts",
      "./i18n/index.ts",
      "./player/playback.ts",
    ]);
  });

  it("elke aanroep staat er letterlijk als import.meta.glob(", () => {
    const wrong = Object.keys(sources)
      .filter((p) => WRONG.test(sources[p] ?? ""))
      .sort();
    expect(wrong).toEqual([]);
    expect((sources["./i18n/index.ts"] ?? "").match(PLAIN)).toHaveLength(2);
    expect((sources["./player/playback.ts"] ?? "").match(PLAIN)).toHaveLength(1);
  });
});
