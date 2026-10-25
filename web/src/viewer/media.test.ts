import { describe, expect, it } from "vitest";
import { browserShows, codecLabel, mediaProblem, viewKind, wantsOriginal } from "./media";

describe("mediaProblem (MediaError -> melding)", () => {
  it("decoderen mislukt: dit formaat speelt hier niet", () => {
    expect(mediaProblem(3)).toBe("unsupported");
    expect(mediaProblem(3, 200)).toBe("unsupported");
  });

  it("bron niet bruikbaar: het adres werkt (formaat) of niet (verlopen)", () => {
    expect(mediaProblem(4, 206)).toBe("unsupported");
    expect(mediaProblem(4, 200)).toBe("unsupported");
    for (const status of [401, 403, 404, 410]) expect(mediaProblem(4, status)).toBe("expired");
    expect(mediaProblem(4, 0)).toBe("network");
    expect(mediaProblem(4, 500)).toBe("network");
    expect(mediaProblem(4)).toBe("unsupported");
  });

  it("netwerk: verlopen als het adres niet meer werkt, anders netwerk", () => {
    expect(mediaProblem(2, 403)).toBe("expired");
    expect(mediaProblem(2, 206)).toBe("network");
    expect(mediaProblem(2)).toBe("network");
  });

  it("afgebroken is geen probleem", () => {
    expect(mediaProblem(1)).toBeNull();
  });
});

describe("viewKind", () => {
  const item = (name: string, mime: string | null) => ({ name, mime });
  it("op het mime-type, met wat de server vond als voorrang", () => {
    expect(viewKind(item("a.jpg", "image/jpeg"), {})).toBe("image");
    expect(viewKind(item("a.pdf", "application/pdf"), {})).toBe("pdf");
    expect(viewKind(item("a.mp4", "video/mp4"), {})).toBe("video");
    expect(viewKind(item("a.mp3", "audio/mpeg"), {})).toBe("audio");
    expect(viewKind(item("a.txt", "text/plain"), {})).toBe("text");
    expect(viewKind(item("a.py", "application/octet-stream"), {})).toBe("text");
    expect(viewKind(item("a.zip", "application/zip"), { entries: [] })).toBe("archive");
    expect(
      viewKind(item("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), {}),
    ).toBe("office");
    expect(viewKind(item("x.bin", "application/octet-stream"), { office: "sheet" })).toBe("office");
    // playlist.mp4 die tekst bleek
    expect(viewKind(item("playlist.mp4", "video/mp4"), { text: "#EXTM3U" })).toBe("text");
    expect(viewKind(item("a.bin", "application/octet-stream"), {})).toBe("none");
  });

  it("wat de browser zelf toont en wat beweegt", () => {
    expect(browserShows("image/jpeg")).toBe(true);
    expect(browserShows("image/heic")).toBe(false);
    expect(browserShows("image/tiff")).toBe(false);
    expect(wantsOriginal("image/gif", { animated: true })).toBe(true);
    expect(wantsOriginal("image/gif", { animated: false })).toBe(false);
    expect(wantsOriginal("image/gif", {})).toBe(true); // nog niet bekend: het origineel
    expect(wantsOriginal("image/jpeg", {})).toBe(false);
  });

  it("codecs als hint", () => {
    expect(
      codecLabel({ video: { codec: "h264", profile: "High" }, audio: { codec: "aac" }, container: "mov,mp4,m4a" }),
    ).toBe("h264 (High) · aac · mov");
    expect(codecLabel({})).toBe("");
  });
});
