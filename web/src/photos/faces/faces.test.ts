import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { loadAreas, resetI18nForTests } from "../../i18n";
import type { PhotoSettings } from "../people/types";
import { FaceCard, FaceGrid } from "./FaceGrid";
import {
  busy,
  confirmIds,
  facesSettings,
  gridKey,
  inChunks,
  isOn,
  LastChoice,
  letters,
  nextOpen,
  peoplePart,
  personSample,
  rememberSettings,
  rowFaces,
  SETTINGS_MS,
  statusLine,
  UNDO_MS,
} from "./logic";
import type { FaceOut, FacesStatus } from "./types";

// Gezichten in de web (fase 6, X2b; Keuze 66). Zonder DOM: het denkwerk staat in logic.ts.

const V = "/api/v1";

function settings(over: Partial<PhotoSettings> = {}): PhotoSettings {
  return {
    location: "mij",
    memories: true,
    ocr_photos: false,
    faces: "uit",
    faces_forbidden: false,
    faces_available: false,
    beeld: "uit",
    ...over,
  };
}

function face(id: string, over: Partial<FaceOut> = {}): FaceOut {
  return { id, node_id: `n-${id}`, box: [0.1, 0.1, 0.2, 0.2], person: null, suggest: null, ...over };
}

function status(over: Partial<FacesStatus> = {}): FacesStatus {
  return {
    setting: "aan",
    forbidden: false,
    analyse: "ok",
    photos: { total: 55000, done: 1234, waiting: 53766, failed: 0, skipped: 0 },
    faces: { found: 3456, named: 12, open: 3444, groups: 5 },
    wiping: false,
    ...over,
  };
}

/** api.request met een antwoord per pad; elke aanroep staat in calls. */
function serve(answers: Record<string, unknown>) {
  return vi.spyOn(api, "request").mockImplementation(async (method: string, path: string) => {
    if (!(path in answers)) throw new Error(`niet verwacht: ${method} ${path}`);
    return answers[path] as never;
  });
}

beforeEach(async () => {
  resetI18nForTests("nl");
  await loadAreas(["photos_faces"]);
  rememberSettings("", null);
});

afterEach(() => {
  vi.restoreAllMocks();
  rememberSettings("", null);
  resetI18nForTests("nl");
});

describe("uit is onzichtbaar: zonder de analyse vraagt de web geen route van gezichten (0.8.0)", () => {
  it("de rij in het infopaneel en 'Misschien ook' vragen alleen GET /me/photo-settings, één keer", async () => {
    const request = serve({ [`${V}/me/photo-settings`]: settings({ faces: "aan" }) });
    expect(await rowFaces("u1", "f1")).toBeNull();
    expect(await rowFaces("u1", "f2")).toBeNull();
    expect(await personSample("u1", "p1")).toBeNull();
    expect(request.mock.calls).toEqual([["GET", `${V}/me/photo-settings`]]);
  });

  it("met gezichten uit (de server kan het wel) ook geen route van gezichten", async () => {
    const request = serve({ [`${V}/me/photo-settings`]: settings({ faces_available: true }) });
    expect(await rowFaces("u1", "f1")).toBeNull();
    expect(await personSample("u1", "p1")).toBeNull();
    expect(request.mock.calls).toEqual([["GET", `${V}/me/photo-settings`]]);
  });

  it("/fotos/personen: niets zonder de analyse, ook niet als de stand 'aan' of 'verboden' zegt", () => {
    expect(peoplePart(settings())).toBe("none");
    expect(peoplePart(settings({ faces: "aan" }))).toBe("none");
    expect(peoplePart(settings({ faces_forbidden: true }))).toBe("none");
    expect(peoplePart(settings({ faces_available: true }))).toBe("off");
    expect(peoplePart(settings({ faces_available: true, faces: "aan" }))).toBe("on");
    expect(peoplePart(settings({ faces_available: true, faces_forbidden: true }))).toBe("forbidden");
    expect(peoplePart(settings({ faces_available: true, faces: "aan", faces_forbidden: true }))).toBe("forbidden");
    expect(isOn(settings({ faces: "aan" }))).toBe(false);
    expect(isOn(settings({ faces_available: true, faces: "aan" }))).toBe(true);
  });
});

describe("de rij Gezichten in het infopaneel", () => {
  it("de eigenaar met gezichten aan: de foto en zijn gezichten, tegelijk gevraagd", async () => {
    const found = [face("g1"), face("g2", { person: { id: "p1", name: "Anna" } })];
    const request = serve({
      [`${V}/me/photo-settings`]: settings({ faces_available: true, faces: "aan" }),
      [`${V}/files/f%2F1`]: { id: "f/1", permission: "owner" },
      [`${V}/files/f%2F1/faces`]: found,
    });
    expect(await rowFaces("u1", "f/1")).toEqual(found);
    expect(request.mock.calls).toEqual([
      ["GET", `${V}/me/photo-settings`],
      ["GET", `${V}/files/f%2F1`],
      ["GET", `${V}/files/f%2F1/faces`],
    ]);
  });

  it("de foto van een ander (met jou gedeeld): geen rij, ook als de API iets gaf", async () => {
    serve({
      [`${V}/me/photo-settings`]: settings({ faces_available: true, faces: "aan" }),
      [`${V}/files/f1`]: { id: "f1", permission: "read" },
      [`${V}/files/f1/faces`]: [],
    });
    expect(await rowFaces("u1", "f1")).toBeNull();
  });

  it("'Misschien ook Anna': zes uitsneden van de eerste pagina", async () => {
    const page = { faces: [face("g1")], cursor: null };
    const request = serve({
      [`${V}/me/photo-settings`]: settings({ faces_available: true, faces: "aan" }),
      [`${V}/people/faces/suggestions?person=p1&limit=6`]: page,
    });
    expect(await personSample("u1", "p1")).toEqual(page);
    expect(request).toHaveBeenCalledTimes(2);
  });
});

describe("de foto-instellingen, gedeeld door de plekken", () => {
  it("hooguit één keer per minuut per gebruiker; een andere gebruiker vraagt opnieuw", async () => {
    const request = serve({ [`${V}/me/photo-settings`]: settings() });
    await facesSettings("u1", 1000);
    await facesSettings("u1", 1000 + SETTINGS_MS);
    expect(request).toHaveBeenCalledTimes(1);
    await facesSettings("u2", 1000 + SETTINGS_MS);
    expect(request).toHaveBeenCalledTimes(2);
    await facesSettings("u2", 1001 + 2 * SETTINGS_MS);
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("na een fout vraagt de volgende opnieuw; na PATCH geldt de nieuwe stand meteen", async () => {
    const request = vi.spyOn(api, "request").mockRejectedValueOnce(new Error("weg"));
    await expect(facesSettings("u1", 0)).rejects.toThrow("weg");
    request.mockResolvedValueOnce(settings());
    expect((await facesSettings("u1", 1)).faces).toBe("uit");
    expect(request).toHaveBeenCalledTimes(2);
    rememberSettings("u1", settings({ faces_available: true, faces: "aan" }), 2);
    expect((await facesSettings("u1", 3)).faces).toBe("aan");
    expect(request).toHaveBeenCalledTimes(2);
  });
});

describe("de regel bovenaan /fotos/personen (Keuze 66)", () => {
  it("nl: zoeken, klaar, wacht, verboden, wissen, en mislukt erachter", () => {
    expect(statusLine(status())).toBe("Gezichten zoeken: 1.234 van 55.000 foto's");
    const klaar = status({ photos: { total: 2100, done: 2100, waiting: 0, failed: 0, skipped: 0 } });
    expect(statusLine(klaar)).toBe("Klaar: 3.456 gezichten in 2.100 foto's");
    expect(statusLine(status({ analyse: "down" }))).toBe("Wacht op de analyse");
    expect(statusLine(status({ forbidden: true, wiping: true }))).toBe(
      "De beheerder heeft gezichtsherkenning uitgezet",
    );
    expect(statusLine(status({ wiping: true }))).toBe("Gezichten worden gewist …");
    const mislukt = status({ photos: { total: 10, done: 8, waiting: 0, failed: 2, skipped: 0 } });
    expect(statusLine(mislukt)).toBe("Klaar: 3.456 gezichten in 8 foto's · 2 foto's mislukt");
    const een = status({ photos: { total: 10, done: 9, waiting: 0, failed: 1, skipped: 0 } });
    expect(statusLine(een)).toBe("Klaar: 3.456 gezichten in 9 foto's · 1 foto mislukt");
  });

  it("en: dezelfde regels in het Engels", async () => {
    resetI18nForTests("en");
    await loadAreas(["photos_faces"]);
    expect(statusLine(status())).toBe("Looking for faces: 1,234 of 55,000 photos");
    expect(statusLine(status({ analyse: "models_missing" }))).toBe("Waiting for the analysis");
    const klaar = status({ photos: { total: 2100, done: 2100, waiting: 0, failed: 3, skipped: 0 } });
    expect(statusLine(klaar)).toBe("Done: 3,456 faces in 2,100 photos · 3 photos failed");
  });

  it("opnieuw vragen (elke 10 s) alleen zolang er werk is", () => {
    expect(busy(status())).toBe(true);
    expect(busy(status({ photos: { total: 1, done: 1, waiting: 0, failed: 0, skipped: 0 } }))).toBe(false);
    expect(busy(status({ photos: { total: 1, done: 1, waiting: 0, failed: 0, skipped: 0 }, wiping: true }))).toBe(true);
    expect(busy(status({ forbidden: true }))).toBe(false);
    expect(busy(status({ setting: "uit" }))).toBe(false);
  });
});

describe("de toetsen in het raster (scope faces)", () => {
  const key = (k: string, mods: Partial<{ ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean }> = {}) => ({
    key: k,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    ...mods,
  });

  it("nl: J ja, N nee, V niet tonen; Y en H doen niets", () => {
    expect(letters("nl")).toEqual({ yes: "j", no: "n", hide: "v" });
    expect(gridKey(key("j"), "suggest", "nl")).toBe("yes");
    expect(gridKey(key("J", { shiftKey: true }), "suggest", "nl")).toBe("yes");
    expect(gridKey(key("n"), "suggest", "nl")).toBe("no");
    expect(gridKey(key("v"), "suggest", "nl")).toBe("hide");
    expect(gridKey(key("y"), "suggest", "nl")).toBeNull();
    expect(gridKey(key("h"), "suggest", "nl")).toBeNull();
  });

  it("en: Y yes, N no, H hide; J en V doen niets", () => {
    expect(gridKey(key("y"), "suggest", "en")).toBe("yes");
    expect(gridKey(key("n"), "suggest", "en")).toBe("no");
    expect(gridKey(key("h"), "suggest", "en")).toBe("hide");
    expect(gridKey(key("j"), "suggest", "en")).toBeNull();
    expect(gridKey(key("v"), "suggest", "en")).toBeNull();
  });

  it("de taal van de app als er geen taal bij staat", async () => {
    expect(gridKey(key("j"), "suggest")).toBe("yes");
    resetI18nForTests("en");
    expect(gridKey(key("j"), "suggest")).toBeNull();
    expect(gridKey(key("y"), "suggest")).toBe("yes");
  });

  it("'Wie is dit?': Spatie selecteert, V verbergt; ja en nee horen bij voorstellen", () => {
    expect(gridKey(key(" "), "group", "nl")).toBe("select");
    expect(gridKey(key(" "), "suggest", "nl")).toBeNull();
    expect(gridKey(key("v"), "group", "nl")).toBe("hide");
    expect(gridKey(key("j"), "group", "nl")).toBeNull();
    expect(gridKey(key("n"), "group", "nl")).toBeNull();
  });

  it("Enter opent, Esc sluit, Ctrl+Z of Cmd+Z maakt ongedaan; met Shift of Alt niet", () => {
    expect(gridKey(key("Enter"), "suggest", "nl")).toBe("open");
    expect(gridKey(key("Escape"), "group", "nl")).toBe("close");
    expect(gridKey(key("z", { ctrlKey: true }), "suggest", "nl")).toBe("undo");
    expect(gridKey(key("Z", { metaKey: true }), "group", "nl")).toBe("undo");
    expect(gridKey(key("z", { ctrlKey: true, shiftKey: true }), "suggest", "nl")).toBeNull();
    expect(gridKey(key("z", { ctrlKey: true, altKey: true }), "suggest", "nl")).toBeNull();
    expect(gridKey(key("j", { ctrlKey: true }), "suggest", "nl")).toBeNull();
    expect(gridKey(key("j", { altKey: true }), "suggest", "nl")).toBeNull();
    expect(gridKey(key("z"), "suggest", "nl")).toBeNull();
  });

  it("pijlen, Home en End bewegen; Tab gaat door", () => {
    expect(gridKey(key("ArrowRight"), "suggest", "nl")).toBe("right");
    expect(gridKey(key("ArrowDown"), "group", "nl")).toBe("down");
    expect(gridKey(key("Home"), "group", "nl")).toBe("home");
    expect(gridKey(key("End"), "suggest", "nl")).toBe("end");
    expect(gridKey(key("Tab"), "suggest", "nl")).toBeNull();
  });
});

describe("bevestigen in 'Wie is dit?' en ongedaan maken", () => {
  it("alleen wat je zag en wat geselecteerd is, in de volgorde van het raster; nooit de hele groep", () => {
    const faces = ["a", "b", "c", "d"].map((x) => face(x));
    expect(confirmIds(faces, new Set(["d", "b", "a"]), new Set(["a", "b", "c"]))).toEqual(["a", "b"]);
    expect(confirmIds(faces, new Set(), new Set(["a"]))).toEqual([]);
    expect(confirmIds(faces, new Set(["a"]), new Set())).toEqual([]);
  });

  it("in stukken van hooguit 200 (anders 422 faces_too_many)", () => {
    const ids = Array.from({ length: 450 }, (_, i) => `g${i}`);
    expect(inChunks(ids).map((c) => c.length)).toEqual([200, 200, 50]);
    expect(inChunks(ids).flat()).toEqual(ids);
    expect(inChunks([])).toEqual([]);
  });

  it("na een keuze naar het volgende gezicht zonder keuze, anders terug, anders geen", () => {
    const done = new Set([1, 2]);
    expect(nextOpen(5, 0, (j) => done.has(j) || j === 0)).toBe(3);
    expect(nextOpen(5, 4, (j) => j >= 3 || done.has(j))).toBe(0);
    expect(nextOpen(2, 0, () => true)).toBe(-1);
  });

  it("Ctrl+Z neemt de laatste keuze één keer, zolang de melding staat", () => {
    const last = new LastChoice();
    const run = vi.fn();
    last.set(run, 1000);
    expect(last.take(1000 + UNDO_MS)).toBe(run);
    expect(last.take(1000 + UNDO_MS)).toBeNull();
    last.set(run, 0);
    expect(last.take(UNDO_MS + 1)).toBeNull();
    last.set(run, 0);
    last.clear();
    expect(last.take(1)).toBeNull();
  });
});

describe("de kaart en het raster (eerste render, zonder DOM)", () => {
  const noop = () => {};
  const empty = () => new Promise<never>(() => {});

  it("een kaart: hooguit zes uitsneden (versiering), de knop met aria-expanded en het aantal", () => {
    const faces = Array.from({ length: 8 }, (_, i) => face(`g${i}`));
    const html = renderToStaticMarkup(
      createElement(FaceCard, { faces, label: "Is dit Anna?", count: "12 foto's", open: false, onOpen: noop, button: noop }),
    );
    expect(html.match(/class="face-crop"/g)).toHaveLength(6);
    expect(html).toContain('<span class="faces-sample" aria-hidden="true">');
    expect(html).toMatch(/<button type="button" class="btn" aria-expanded="false" aria-describedby="([^"]+)-n">Is dit Anna\?<\/button>/);
    expect(html).toMatch(/<span id="[^"]+-n" class="muted">12 foto&#x27;s<\/span>/);
  });

  it("'Is dit Anna?': een raster met de toetsen in de hulp (nl en en), en Sluiten", async () => {
    const props = { title: "Is dit Anna?", mode: "suggest" as const, person: { id: "p1", name: "Anna" }, load: empty, onClose: noop };
    const html = renderToStaticMarkup(createElement(FaceGrid, props));
    expect(html).toMatch(/<div role="grid" aria-labelledby="[^"]+-kop" aria-describedby="[^"]+-hulp" class="faces-cells">/);
    expect(html).toContain("J: ja, N: nee, V: niet tonen.");
    expect(html).toContain(">Sluiten</button>");
    expect(html).not.toContain("aria-multiselectable");
    resetI18nForTests("en");
    await loadAreas(["photos_faces"]);
    expect(renderToStaticMarkup(createElement(FaceGrid, props))).toContain("Y: yes, N: no, H: hide.");
  });

  it("'Wie is dit?': meer te selecteren, met Spatie in de hulp", () => {
    const html = renderToStaticMarkup(
      createElement(FaceGrid, { title: "Wie is dit?", mode: "group", load: empty, onClose: noop }),
    );
    expect(html).toContain('aria-multiselectable="true"');
    expect(html).toContain("Spatie: selecteren of niet. V: niet tonen.");
  });
});

describe("de teksten van gezichten", () => {
  it("nl en en hebben dezelfde sleutels en geen open plek na invullen", async () => {
    const nl = (await import("../../i18n/areas/photos_faces.nl")).default;
    const en = (await import("../../i18n/areas/photos_faces.en")).default;
    const keys = (o: object, p = ""): string[] =>
      Object.entries(o).flatMap(([k, v]) => (typeof v === "string" ? [`${p}${k}`] : keys(v, `${p}${k}.`)));
    expect(keys(en)).toEqual(keys(nl));
    expect(nl.switch.consent).toContain("biometrische data");
    expect(nl.switch.consent).toContain("Reservekopieën van het platform bevatten ze niet");
    expect(nl.admin.notMeasured).toBe("niet gemeten");
  });
});
