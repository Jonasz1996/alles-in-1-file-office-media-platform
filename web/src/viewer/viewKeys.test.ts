// De sleutels van de viewer (viewKeys.ts, review txt punt 1): een nieuwe etag mag een tekst die
// je bewerkt niet weggooien. TextView krijgt fileKeyOf, en het vorige detail blijft staan tot het
// nieuwe er is.

import { describe, expect, it } from "vitest";
import { fileNode } from "../browser/testNodes";
import { withViewing } from "./param";
import { fileKeyOf, keyOf, shownLoaded } from "./viewKeys";

const before = fileNode({ id: "t1", name: "notities.txt", etag: '"a"' });
const after = { ...before, etag: '"b"' };

describe("de sleutels", () => {
  it("een nieuwe etag: een andere inhoud (keyOf), maar hetzelfde bestand (fileKeyOf)", () => {
    expect(keyOf(after)).not.toBe(keyOf(before));
    expect(fileKeyOf(after)).toBe(fileKeyOf(before));
    // Een eerdere versie is een ander bestand om te tonen
    expect(fileKeyOf({ ...before, version: { id: "v1", label: "v1" } })).not.toBe(fileKeyOf(before));
  });

  it("het vorige detail blijft voor een tekst tot het nieuwe er is; voor de rest niet", () => {
    const loaded = { key: keyOf(before), file: fileKeyOf(before), detail: { kind: "text" } };
    const text = (d: { kind: string }) => d.kind === "text";
    expect(shownLoaded(loaded, keyOf(before), fileKeyOf(before), text)).toBe(loaded);
    expect(shownLoaded(loaded, keyOf(after), fileKeyOf(after), text)).toBe(loaded);
    expect(shownLoaded({ ...loaded, detail: { kind: "image" } }, keyOf(after), fileKeyOf(after), text)).toBeNull();
    // Een ander bestand: laden
    const other = fileNode({ id: "t2", etag: '"a"' });
    expect(shownLoaded(loaded, keyOf(other), fileKeyOf(other), text)).toBeNull();
    // Een fout van het vorige: niet bewaren
    expect(shownLoaded({ key: loaded.key, file: loaded.file, error: new Error("x") }, keyOf(after), fileKeyOf(after), text)).toBeNull();
    expect(shownLoaded(null, keyOf(after), fileKeyOf(after), text)).toBeNull();
  });
});

describe("de bestanden voor de viewer met een filter (review txt, punt 5)", () => {
  it("het bestand dat open moet, komt erbij als het filter het verbergt", () => {
    expect(withViewing(["a", "b"], ["a", "b", "nieuw"], "nieuw")).toEqual(["a", "b", "nieuw"]);
    expect(withViewing([], ["a", "nieuw"], "nieuw")).toEqual(["nieuw"]);
  });

  it("anders blijft de lijst zoals hij is", () => {
    const shown = ["a", "b"];
    expect(withViewing(shown, ["a", "b"], "a")).toBe(shown);
    expect(withViewing(shown, ["a", "b"], null)).toBe(shown);
    expect(withViewing(shown, ["a", "b"], "weg")).toBe(shown); // niet in deze map
  });
});
