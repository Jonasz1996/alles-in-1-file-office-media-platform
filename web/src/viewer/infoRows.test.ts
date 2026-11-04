import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { showLikeRow, showPhotoRows } from "./infoRows";
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

describe("de plek LikeRow (Z0b): leeg tot X3 hem vult", () => {
  it("toont niets, ook bij een foto", () => {
    const item: ViewerItem = { id: "n1", name: "strand.jpg", mime: "image/jpeg", size: 1, modified_at: "2024-06-01T12:00:00Z" };
    expect(renderToStaticMarkup(createElement(LikeRow, { item, detail: undefined, kind: "image" }))).toBe("");
  });
});
