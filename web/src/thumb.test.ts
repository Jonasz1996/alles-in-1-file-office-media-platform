import { describe, expect, it } from "vitest";
import { formatDuration, mayRenew, playerFor } from "./PreviewPanel";
import { category } from "./Thumb";

describe("category", () => {
  it("kiest het soort op het mime-type", () => {
    const file = (mime: string | null) => ({ kind: "file" as const, mime });
    expect(category({ kind: "dir", mime: null })).toBe("folder");
    expect(category(file("image/jpeg"))).toBe("image");
    expect(category(file("video/mp4"))).toBe("video");
    expect(category(file("audio/mpeg"))).toBe("audio");
    expect(category(file("application/pdf"))).toBe("pdf");
    expect(category(file("application/zip"))).toBe("archive");
    expect(category(file("application/gzip"))).toBe("archive");
    expect(category(file("text/plain"))).toBe("document");
    expect(category(file("application/vnd.openxmlformats-officedocument.wordprocessingml.document"))).toBe(
      "document",
    );
    expect(category(file("application/octet-stream"))).toBe("other");
    expect(category(file(null))).toBe("other");
  });
});

describe("formatDuration", () => {
  it("minuten en seconden, met uren als dat nodig is", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(83.4)).toBe("1:23");
    expect(formatDuration(3723)).toBe("1:02:03");
  });
});

describe("playerFor", () => {
  it("een speler voor video en audio, behalve als de inhoud tekst of een archief bleek", () => {
    expect(playerFor("video", {})).toBe("video");
    expect(playerFor("audio", { duration: 3 })).toBe("audio");
    expect(playerFor("video", { text: "#EXTM3U" })).toBeNull(); // playlist.mp4 die tekst is
    expect(playerFor("video", { entries: [] })).toBeNull();
    expect(playerFor("image", {})).toBeNull();
  });
});

describe("mayRenew", () => {
  it("hooguit drie nieuwe links kort na elkaar, na een stille minuut weer wel", () => {
    const state = { count: 0, at: 0 };
    let now = 1_000_000;
    expect([1, 2, 3, 4].map(() => mayRenew(state, (now += 1000)))).toEqual([true, true, true, false]);
    // Lang afspelen: de link verloopt elke tien minuten, dat blijft gewoon werken
    for (let i = 0; i < 5; i++) expect(mayRenew(state, (now += 10 * 60_000))).toBe(true);
  });
});
