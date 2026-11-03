import { describe, expect, it } from "vitest";
import { showPhotoRows } from "./infoRows";
import type { ViewKind } from "./media";

describe("Plek en Personen in het infopaneel (fase 6, Z0)", () => {
  it("alleen bij een foto of video, ingelogd", () => {
    const kinds: ViewKind[] = ["image", "pdf", "video", "audio", "text", "office", "archive", "none"];
    expect(kinds.filter((k) => showPhotoRows(k, {}, true))).toEqual(["image", "video"]);
  });

  it("nooit via een publieke link (Keuze 27)", () => {
    expect(showPhotoRows("image", {}, false)).toBe(false);
    expect(showPhotoRows("video", {}, false)).toBe(false);
  });

  it("niet bij een eerdere versie", () => {
    expect(showPhotoRows("image", { version: { id: "v1", label: "Versie 2" } }, true)).toBe(false);
  });
});
