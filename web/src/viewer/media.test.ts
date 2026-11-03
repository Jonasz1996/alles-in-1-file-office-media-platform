import { describe, expect, it } from "vitest";
import { browserShows, codecLabel, mediaProblem, pictureMissing, viewKind, wantsOriginal } from "./media";

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

describe("pictureMissing (alleen geluid, geen fout)", () => {
  const hevc = { video: { codec: "hevc", profile: "Main 10" }, audio: { codec: "ac3" }, container: "matroska,webm" };

  it("een videospoor volgens de server, maar geen beeld in de browser: dit formaat speelt hier niet", () => {
    // HEVC zonder GPU naast geluid dat de browser wel kan: geen MediaError (CI op 1a6dac0)
    expect(pictureMissing(hevc, 0)).toBe(true);
    expect(pictureMissing({ video: { codec: "HEVC" } }, 0)).toBe(true);
    expect(pictureMissing({ video: { codec: "mpeg4" }, audio: { codec: "mp3" } }, 0)).toBe(true);
  });

  it("met beeld speelt hij gewoon", () => {
    expect(pictureMissing(hevc, 1280)).toBe(false);
    expect(pictureMissing({ video: { codec: "vp9" } }, 640)).toBe(false);
  });

  it("zonder videospoor in de meta (nog geen preview, of alleen geluid) geen melding", () => {
    expect(pictureMissing({}, 0)).toBe(false);
    expect(pictureMissing({ audio: { codec: "opus" }, container: "matroska,webm" }, 0)).toBe(false);
    expect(pictureMissing({ video: {} }, 0)).toBe(false);
  });

  it("een hoes is geen beeld om af te spelen (Keuze 19)", () => {
    for (const codec of ["mjpeg", "png", "bmp", "gif", "webp", "tiff", "PNG"]) {
      expect(pictureMissing({ video: { codec }, audio: { codec: "aac" } }, 0)).toBe(false);
    }
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

  it("doc, xls, ppt en rtf: alleen met een PDF van de editor (fase 4) de weergave van kantoordocumenten", () => {
    const doc = item("oud.doc", "application/msword");
    expect(viewKind(doc, {})).toBe("none"); // zonder office: precies fase 3
    for (const state of ["ready", "pending", "ask"] as const) expect(viewKind(doc, { pdf_state: state })).toBe("office");
    for (const state of ["failed", "none", "unsafe"] as const) expect(viewKind(doc, { pdf_state: state })).toBe("none");
    expect(viewKind(item("a.rtf", "application/rtf"), { pdf_state: "ready" })).toBe("office");
    expect(viewKind(item("b.ppt", "application/vnd.ms-powerpoint"), { pdf_state: "pending" })).toBe("office");
  });

  it("wat de browser zelf toont en wat beweegt", () => {
    expect(browserShows("image/jpeg")).toBe(true);
    expect(browserShows("image/heic")).toBe(false);
    expect(browserShows("image/tiff")).toBe(false);
    // Camera-RAW, PSD en JPEG 2000: ook de grote preview (display), niet het origineel
    const raw = ["image/x-fuji-raf", "image/x-olympus-orf", "image/x-panasonic-rw2", "image/x-pentax-pef"];
    for (const m of [...raw, "image/x-canon-cr3", "image/jp2", "image/vnd.adobe.photoshop"]) {
      expect(browserShows(m)).toBe(false);
    }
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
