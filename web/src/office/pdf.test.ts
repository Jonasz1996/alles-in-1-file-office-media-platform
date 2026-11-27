import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import { toasts } from "../ui/toast";
import { CONVERT_FORMATS, canPdf, convertible, downloadPdf, extensionOf, pdfName } from "./pdf";
import type { OfficeStatus, OfficeTarget } from "./types";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const on: OfficeStatus = {
  enabled: true,
  editing: true,
  state: "ok",
  edit_formats: ["docx", "xlsx", "pptx"],
  new_kinds: ["docx", "xlsx", "pptx"],
  max_bytes: 104857600,
};

const target = (name: string, rest: Partial<OfficeTarget> = {}): OfficeTarget => ({
  id: "f1",
  name,
  mime: null,
  size: 1000,
  kind: "file",
  ...rest,
});

describe("welke bestanden de editor omzet", () => {
  it("op de extensie, in kleine letters", () => {
    expect(extensionOf("Rapport 2024.DOCX")).toBe("docx");
    expect(extensionOf("zonder")).toBe("");
    expect(extensionOf(".verborgen")).toBe("");
    expect(extensionOf("a.tar.gz")).toBe("gz");
    for (const ext of ["docx", "xlsx", "pptx", "odt", "ods", "odp", "doc", "xls", "ppt", "rtf"]) {
      expect(convertible(`a.${ext}`)).toBe(true);
    }
    expect(CONVERT_FORMATS).toContain("pptm");
    for (const name of ["a.pdf", "a.txt", "a.csv", "a.jpg", "docx", "a.docx.zip"]) expect(convertible(name)).toBe(false);
  });

  it("de naam van de PDF zoals de server de bijlage noemt", () => {
    expect(pdfName("Rapport 2024.docx")).toBe("Rapport 2024.pdf");
    expect(pdfName("oud.DOC")).toBe("oud.pdf");
    expect(pdfName("a.b.pptx")).toBe("a.b.pdf");
    expect(pdfName("zonder")).toBe("zonder.pdf");
    expect(pdfName(".docx")).toBe(".docx.pdf");
  });
});

describe("canPdf (de actie in de lijst en de knop in de viewer)", () => {
  it("zonder office of bij een oudere server: niet te zien, precies fase 3", () => {
    expect(canPdf(target("a.docx"), null)).toEqual({ visible: false, reason: null });
    expect(canPdf(target("a.docx"), { ...on, enabled: false, editing: false, state: "off" })).toEqual({
      visible: false,
      reason: null,
    });
  });

  it("voor elk soort dat de editor omzet, ook doc, xls, ppt en rtf (die hij niet bewerkt)", () => {
    for (const name of ["a.docx", "b.xlsx", "c.pptx", "d.odt", "e.doc", "f.xls", "g.ppt", "h.rtf"]) {
      expect(canPdf(target(name), on)).toEqual({ visible: true, reason: null });
    }
    expect(canPdf(target("a.pdf"), on).visible).toBe(false);
    expect(canPdf(target("a.txt"), on).visible).toBe(false);
  });

  it("ook als de editor nu niet bereikbaar is: een PDF die er al is, werkt dan ook", () => {
    expect(canPdf(target("a.docx"), { ...on, editing: false, state: "down" })).toEqual({ visible: true, reason: null });
  });

  it("ook met office uit op /beheer: uit stopt alleen nieuwe PDF's, de server beslist", () => {
    expect(canPdf(target("a.docx"), { ...on, enabled: false, editing: false })).toEqual({ visible: true, reason: null });
    // Uit op /beheer terwijl DS start of weg is: een PDF die er al is, werkt dan ook
    for (const state of ["starting", "down"] as const) {
      expect(canPdf(target("a.docx"), { ...on, enabled: false, editing: false, state })).toEqual({
        visible: true,
        reason: null,
      });
    }
  });

  it("met een adres maar zonder werkende instellingen: niet te zien, elke klik gaf 503 office_off", () => {
    // Wat office/status zegt met PLATFORM_OFFICE_URL maar zonder motor (o5.md afwijking 9)
    expect(canPdf(target("a.docx"), { ...on, enabled: false, editing: false, state: "misconfigured" })).toEqual({
      visible: false,
      reason: null,
    });
    // Aan op /beheer en DS weigert ons token: een PDF die er al is, werkt wel
    expect(canPdf(target("a.docx"), { ...on, editing: false, state: "misconfigured" })).toEqual({
      visible: true,
      reason: null,
    });
  });

  it("met leesrecht ook: downloaden mag wie mag lezen", () => {
    expect(canPdf(target("a.docx", { permission: "read" }), on)).toEqual({ visible: true, reason: null });
    // Een item van de viewer heeft geen kind
    expect(canPdf(target("a.docx", { kind: undefined }), on).visible).toBe(true);
  });

  it("niet voor een map of een eerdere versie; in de prullenbak zichtbaar maar het kan niet", () => {
    expect(canPdf(target("map.docx", { kind: "dir" }), on).visible).toBe(false);
    expect(canPdf(target("album.docx", { kind: "album" }), on).visible).toBe(false);
    expect(canPdf(target("a.docx", { version: { id: "v1" } }), on).visible).toBe(false);
    expect(canPdf(target("a.docx", { deleted_at: "2026-10-01T00:00:00Z" }), on)).toEqual({
      visible: true,
      reason: "trashed",
    });
  });
});

describe("downloadPdf", () => {
  it("vraagt de link en downloadt met de naam van de PDF", async () => {
    const request = vi
      .spyOn(api, "request")
      .mockResolvedValue({ url: "/api/v1/office/pdf/f1?token=t", expires_in: 600 } as never);
    const click = vi.fn();
    await downloadPdf({ id: "f/1", name: "Rapport 2024.docx" }, { click });
    expect(request).toHaveBeenCalledWith("POST", "/api/v1/office/files/f%2F1/pdf");
    expect(click).toHaveBeenCalledWith("/api/v1/office/pdf/f1?token=t", "Rapport 2024.pdf");
  });

  it("een fout gaat naar de haak (die meldt hem), zonder te downloaden", async () => {
    vi.spyOn(api, "request").mockRejectedValue(new ApiError(409, "De PDF wordt nog gemaakt", "pdf_pending"));
    const click = vi.fn();
    await expect(downloadPdf({ id: "f1", name: "a.docx" }, { click })).rejects.toMatchObject({ code: "pdf_pending" });
    expect(click).not.toHaveBeenCalled();
  });

  it("duurt het even (de server zet hem eerst om), dan een melding tot hij er is", async () => {
    vi.useFakeTimers();
    let answer: (v: unknown) => void = () => {};
    vi.spyOn(api, "request").mockReturnValue(new Promise((resolve) => (answer = resolve)) as never);
    const click = vi.fn();
    const done = downloadPdf({ id: "f1", name: "a.docx" }, { click, slowMs: 1500 });
    await vi.advanceTimersByTimeAsync(1000);
    expect(toasts.getSnapshot().some((x) => x.id === "pdf-f1")).toBe(false);
    await vi.advanceTimersByTimeAsync(600);
    // De teksten van de viewer laden; dan de melding
    await vi.waitFor(() => expect(toasts.getSnapshot().some((x) => x.id === "pdf-f1")).toBe(true));
    const shown = toasts.getSnapshot().find((x) => x.id === "pdf-f1");
    expect(shown?.message).toBe("De PDF wordt gemaakt…");
    answer({ url: "/pdf", expires_in: 600 });
    await done;
    expect(click).toHaveBeenCalledOnce();
    expect(toasts.getSnapshot().some((x) => x.id === "pdf-f1")).toBe(false);
  });

  it("snel klaar: geen melding", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "request").mockResolvedValue({ url: "/pdf", expires_in: 600 } as never);
    await downloadPdf({ id: "f2", name: "a.docx" }, { click: () => {} });
    await vi.advanceTimersByTimeAsync(5000);
    expect(toasts.getSnapshot().some((x) => x.id === "pdf-f2")).toBe(false);
  });
});
