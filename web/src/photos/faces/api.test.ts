import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import facesCatalog from "../../i18n/errors/faces";
import * as faces from "./api";
import { CROP_SIZES, FACES_ERROR_CODES, FACES_PAGE, MAX_FACES } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("de aanroepen van gezichten automatisch (X2, vastgelegd in Z0b)", () => {
  it("elke functie gebruikt de methode, het pad en de body van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    await faces.facesStatus();
    await faces.faceSuggestions();
    await faces.faceSuggestions("c1");
    await faces.personFaceSuggestions("p/1");
    await faces.personFaceSuggestions("p1", "c2", FACES_PAGE);
    await faces.faceGroups();
    await faces.faceGroups("c3");
    await faces.faceGroup("r1:4");
    await faces.faceGroup("r1:4", "c4");
    await faces.confirmFaces({ faces: ["g1", "g2"], person_id: "p1" });
    await faces.confirmFaces({ faces: ["g3"], name: "Anna" });
    await faces.rejectFaces({ faces: ["g1"], person_id: "p1" });
    await faces.hideFaces(["g4"]);
    await faces.resetFaces(["g1", "g4"]);
    await faces.wipeUnnamedFaces();
    await faces.fileFaces("f/1");
    await faces.rescanFaces("f1");

    const V = "/api/v1";
    const F = `${V}/people/faces`;
    expect(request.mock.calls).toEqual([
      ["GET", `${F}/status`],
      ["GET", `${F}/suggestions`],
      ["GET", `${F}/suggestions?cursor=c1`],
      ["GET", `${F}/suggestions?person=p%2F1`],
      ["GET", `${F}/suggestions?person=p1&cursor=c2&limit=60`],
      ["GET", `${F}/groups`],
      ["GET", `${F}/groups?cursor=c3`],
      ["GET", `${F}/groups/r1%3A4`],
      ["GET", `${F}/groups/r1%3A4?cursor=c4`],
      ["POST", `${F}/confirm`, { faces: ["g1", "g2"], person_id: "p1" }],
      ["POST", `${F}/confirm`, { faces: ["g3"], name: "Anna" }],
      ["POST", `${F}/reject`, { faces: ["g1"], person_id: "p1" }],
      ["POST", `${F}/hide`, { faces: ["g4"] }],
      ["POST", `${F}/reset`, { faces: ["g1", "g4"] }],
      ["POST", `${F}/wipe-unnamed`],
      ["GET", `${V}/files/f%2F1/faces`],
      ["POST", `${V}/files/f1/faces/rescan`],
    ]);
  });

  it("een uitsnede (96 of 192 px) gaat met de inlog in de header, als blob; nooit een adres voor een <img>", async () => {
    // Review Z0b, veilig V2 en contract F2: de route wil de inlog als Bearer-header (zoals elke
    // route van X2), en die stuurt een <img> niet mee. Dus haalt de web hem met rawRequest.
    const raw = vi
      .spyOn(api, "rawRequest")
      .mockImplementation(async () => new Response(new Blob(["webp"], { type: "image/webp" })));
    expect(CROP_SIZES).toEqual([96, 192]);
    const blobs = await Promise.all(CROP_SIZES.map((size) => faces.faceCrop("g/1", size)));
    expect(raw.mock.calls).toEqual([
      ["/api/v1/people/faces/g%2F1/crop?size=96"],
      ["/api/v1/people/faces/g%2F1/crop?size=192"],
    ]);
    expect(blobs.map((b) => b.type)).toEqual(["image/webp", "image/webp"]);
    expect(Object.keys(faces).filter((name) => /url/i.test(name))).toEqual([]);
  });

  it("de grenzen van het contract: 200 gezichten per verzoek, 60 per pagina", () => {
    expect(MAX_FACES).toBe(200);
    expect(FACES_PAGE).toBe(60);
  });

  it("de catalogus faces.ts heeft precies de foutcodes van gezichten", () => {
    expect([...FACES_ERROR_CODES].sort()).toEqual([
      "faces_forbidden",
      "faces_gone",
      "faces_off",
      "faces_too_many",
      "faces_unavailable",
    ]);
    expect(Object.keys(facesCatalog).sort()).toEqual([...FACES_ERROR_CODES].sort());
    for (const { en } of Object.values(facesCatalog)) {
      expect(en.trim()).not.toBe("");
      expect(en).not.toMatch(/[{}]/); // de API geeft geen params mee: een {x} bleef letterlijk staan
    }
  });
});
