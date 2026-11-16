// Het dialoog "Wissen" op /fotos/personen/:id (Z6; I2, 0.8.1): de zin over de bevestigde gezichten
// staat er alleen als gezichten voor jou aanstaan (X2, bouwontwerp "De web"). Uit, verboden of
// zonder de analyse blijft het de zin van 0.8.0; mislukt GET /me/photo-settings, dan ook.

import { afterEach, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests } from "../../i18n";
import { deleteMessage } from "./PersonPage";
import personPage from "./PersonPage.tsx?raw";
import type { PhotoSettings } from "./types";

const AAN: PhotoSettings = {
  location: "mij",
  memories: true,
  ocr_photos: false,
  faces: "aan",
  faces_forbidden: false,
  faces_available: true,
  beeld: "uit",
};

const NL = "Anna verdwijnt van al je foto's. De foto's zelf blijven.";
const NL_FACES = "De gezichten die je als Anna bevestigde, worden gewist en niet opnieuw voorgesteld.";

afterEach(() => {
  resetI18nForTests("nl");
});

describe("het dialoog Wissen van een persoon", () => {
  it("nl: met gezichten aan de zin erbij, anders de zin van 0.8.0", async () => {
    resetI18nForTests("nl");
    await loadAreas(["photos_people"]);
    expect(deleteMessage("Anna", AAN)).toBe(`${NL} ${NL_FACES}`);
    // Gezichten uit voor jou, verboden door de beheerder, of de analyse draait niet (0.8.0)
    expect(deleteMessage("Anna", { ...AAN, faces: "uit" })).toBe(NL);
    expect(deleteMessage("Anna", { ...AAN, faces_forbidden: true })).toBe(NL);
    expect(deleteMessage("Anna", { ...AAN, faces_available: false })).toBe(NL);
    // De stand kwam niet: niets erbij
    expect(deleteMessage("Anna", null)).toBe(NL);
  });

  it("en: dezelfde regel in het Engels", async () => {
    resetI18nForTests("en");
    await loadAreas(["photos_people"]);
    const en = "Anna disappears from all your photos. The photos themselves stay.";
    expect(deleteMessage("Anna", AAN)).toBe(`${en} The faces you confirmed as Anna are erased and not suggested again.`);
    expect(deleteMessage("Anna", { ...AAN, faces: "uit" })).toBe(en);
  });

  it("vraagt de stand pas bij het wissen, uit de api van personen (niet uit src/photos/faces/)", () => {
    expect(personPage).toContain("const settings = await photoSettings().catch(() => null);");
    expect(personPage).toContain("message: deleteMessage(person.name, settings),");
    expect(personPage).not.toMatch(/from "\.\.\/faces\/(?!PersonFaces")/);
    // Eén plek vraagt de instellingen: in remove(), voor het dialoog
    expect(personPage.match(/photoSettings\(\)/g)).toHaveLength(1);
  });
});
