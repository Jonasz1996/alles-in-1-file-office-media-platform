import { describe, expect, it } from "vitest";
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
