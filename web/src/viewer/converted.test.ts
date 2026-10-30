import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { convertedView, POLL_DELAYS, POLL_LIMIT, POLL_MAX_DELAY, pollDelay, requestPreview } from "./converted";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("convertedView (wat de viewer met een kantoordocument doet)", () => {
  it("pagina's zodra /document een PDF geeft, ook via een publieke link", () => {
    expect(convertedView({ kind: "pdf", pdf_state: "ready" }, true)).toBe("pages");
    expect(convertedView({ kind: "pdf", pdf_state: "ready" }, false)).toBe("pages");
    // Een nieuwe poging loopt, maar de vorige PDF is er nog
    expect(convertedView({ kind: "pdf", pdf_state: "pending" }, true)).toBe("pages");
  });

  it("wordt gemaakt: de eenvoudige weergave met een melding", () => {
    expect(convertedView({ kind: "office", pdf_state: "pending" }, true)).toBe("pending");
    expect(convertedView({ kind: "office", pdf_state: "pending" }, false)).toBe("pending");
  });

  it("ask: de knop alleen voor wie ingelogd is; een publieke link start nooit een conversie", () => {
    expect(convertedView({ kind: "office", pdf_state: "ask" }, true)).toBe("ask");
    expect(convertedView({ kind: "office", pdf_state: "ask" }, false)).toBe("simple");
  });

  it("zonder office, mislukt, met wachtwoord of onveilig: de eenvoudige weergave (fase 3)", () => {
    for (const state of [undefined, null, "failed", "none", "unsafe"] as const) {
      expect(convertedView({ kind: "office", pdf_state: state }, true)).toBe("simple");
    }
  });
});

describe("requestPreview", () => {
  it("POST /office/files/{id}/convert, voor een eerdere versie met ?version=", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({ pdf_state: "pending" } as never);
    await expect(requestPreview({ id: "f1" })).resolves.toEqual({ pdf_state: "pending" });
    expect(request).toHaveBeenLastCalledWith("POST", "/api/v1/office/files/f1/convert");
    await requestPreview({ id: "f 1", version: { id: "v/2", label: "2" } });
    expect(request).toHaveBeenLastCalledWith("POST", "/api/v1/office/files/f%201/convert?version=v%2F2");
  });

  it("een fout (te vaak, te groot, editor weg) gaat naar de knop", async () => {
    vi.spyOn(api, "request").mockRejectedValue(new Error("rate_limited"));
    await expect(requestPreview({ id: "f1" })).rejects.toThrow("rate_limited");
  });
});

describe("pollDelay (opnieuw kijken zolang de PDF gemaakt wordt)", () => {
  it("eerst snel, dan steeds minder vaak, en na de grens niet meer", () => {
    expect(POLL_DELAYS.map((_, n) => pollDelay(n))).toEqual([...POLL_DELAYS]);
    expect(pollDelay(POLL_DELAYS.length)).toBe(POLL_MAX_DELAY);
    expect(pollDelay(POLL_LIMIT - 1)).toBe(POLL_MAX_DELAY);
    expect(pollDelay(POLL_LIMIT)).toBeNull();
    expect(pollDelay(POLL_LIMIT + 5)).toBeNull();
  });

  it("samen ruim tien minuten: langer dan een conversie mag duren", () => {
    let total = 0;
    for (let n = 0; pollDelay(n) !== null; n++) total += pollDelay(n) ?? 0;
    expect(total).toBeGreaterThan(10 * 60_000);
    expect(total).toBeLessThan(15 * 60_000);
  });
});
