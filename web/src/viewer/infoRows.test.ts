import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { showFacesRow, showLikeRow, showPhotoRows } from "./infoRows";
import LikeRow from "./LikeRow";
import type { ViewKind } from "./media";
import type { ViewerItem } from "./types";

const KINDS: ViewKind[] = ["image", "pdf", "video", "audio", "text", "office", "archive", "none"];

describe("Plek en Personen in het infopaneel (fase 6, Z0)", () => {
  it("alleen bij een foto of video, ingelogd", () => {
    expect(KINDS.filter((k) => showPhotoRows(k, {}, true))).toEqual(["image", "video"]);
  });

  it("nooit via een publieke link (Keuze 27)", () => {
    expect(showPhotoRows("image", {}, false)).toBe(false);
    expect(showPhotoRows("video", {}, false)).toBe(false);
  });

  it("niet bij een eerdere versie", () => {
    expect(showPhotoRows("image", { version: { id: "v1", label: "Versie 2" } }, true)).toBe(false);
  });
});

describe("Meer zoals deze in het infopaneel (fase 6, Z0b voor X3)", () => {
  it("alleen bij een foto, ingelogd: geen video (Keuze 71)", () => {
    expect(KINDS.filter((k) => showLikeRow(k, {}, true))).toEqual(["image"]);
  });

  it("nooit via een publieke link, en niet bij een eerdere versie", () => {
    expect(KINDS.filter((k) => showLikeRow(k, {}, false))).toEqual([]);
    expect(showLikeRow("image", { version: { id: "v1", label: "Versie 2" } }, true)).toBe(false);
  });
});

describe("de gezichten in het infopaneel (fase 6, Z0b voor X2; review contract F3)", () => {
  it("alleen bij een foto, ingelogd: geen video (Keuze 60)", () => {
    expect(KINDS.filter((k) => showFacesRow(k, {}, true))).toEqual(["image"]);
  });

  it("nooit via een publieke link, en niet bij een eerdere versie", () => {
    expect(KINDS.filter((k) => showFacesRow(k, {}, false))).toEqual([]);
    expect(showFacesRow("image", { version: { id: "v1", label: "Versie 2" } }, true)).toBe(false);
  });
});

// X3b vult de plek: zonder de stand van GET /photos/beeld (zonder DOM vraagt hij hem niet, en met
// de analyse uit is het 501) toont hij niets, zoals in 0.8.0. De rest staat in search/beeld/beeld.test.ts.
describe("de plek LikeRow (Z0b, gevuld door X3b): leeg zolang zoeken op beeld niet kan", () => {
  it("toont niets, ook bij een foto", () => {
    const item: ViewerItem = { id: "n1", name: "strand.jpg", mime: "image/jpeg", size: 1, modified_at: "2024-06-01T12:00:00Z" };
    expect(renderToStaticMarkup(createElement(LikeRow, { item, detail: undefined, kind: "image" }))).toBe("");
  });
});
