import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import peopleCatalog from "../../i18n/errors/people";
import * as people from "./api";
import { PEOPLE_ERROR_CODES } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("de aanroepen van personen, herinneringen en slimme albums (§3.4, Z6)", () => {
  it("elke functie gebruikt de methode en het pad van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    await people.people();
    await people.people(true);
    await people.createPerson("Anna");
    await people.updatePerson("p/1", { name: "Anna B", hidden: false, cover: "n1" });
    await people.mergePerson("p1", "p2");
    await people.deletePerson("p1");
    await people.personPhotos("p1");
    await people.personPhotos("p1", "c2");
    await people.filePeople("f1");
    await people.addFilePerson("f1", { person_id: "p1", box: [0.1, 0.2, 0.3, 0.4] });
    await people.addFilePerson("f1", { name: "Bram" });
    await people.removeFilePerson("f1", "p1");
    await people.memories();
    await people.memories("2024-02-29");
    await people.memoryHides();
    await people.hideMemories({ kind: "folder", ref: "map1" });
    await people.unhideMemories("day", "02-29");
    await people.photoSettings();
    await people.updatePhotoSettings({ location: "uit" });
    await people.updatePhotoSettings({ faces: "aan", beeld: "uit" });
    await people.setAlbumRule("a1", { rule: { type: ["image"], person: ["p1"] } });
    await people.setAlbumRule("a1", { rule: { q: "strand", country: "NL" }, share_new: false });
    await people.confirmAlbumRule("a1", true);
    await people.deleteAlbumRule("a1");

    const V = "/api/v1";
    expect(request.mock.calls).toEqual([
      ["GET", `${V}/people?hidden=0`],
      ["GET", `${V}/people?hidden=1`],
      ["POST", `${V}/people`, { name: "Anna" }],
      ["PATCH", `${V}/people/p%2F1`, { name: "Anna B", hidden: false, cover: "n1" }],
      ["POST", `${V}/people/p1/merge`, { into: "p2" }],
      ["DELETE", `${V}/people/p1`],
      ["GET", `${V}/people/p1/photos`],
      ["GET", `${V}/people/p1/photos?cursor=c2`],
      ["GET", `${V}/files/f1/people`],
      ["POST", `${V}/files/f1/people`, { person_id: "p1", box: [0.1, 0.2, 0.3, 0.4] }],
      ["POST", `${V}/files/f1/people`, { name: "Bram" }],
      ["DELETE", `${V}/files/f1/people/p1`],
      ["GET", `${V}/photos/memories`],
      ["GET", `${V}/photos/memories?day=2024-02-29`],
      ["GET", `${V}/photos/memories/hide`],
      ["POST", `${V}/photos/memories/hide`, { kind: "folder", ref: "map1" }],
      ["DELETE", `${V}/photos/memories/hide/day/02-29`],
      ["GET", `${V}/me/photo-settings`],
      ["PATCH", `${V}/me/photo-settings`, { location: "uit" }],
      ["PATCH", `${V}/me/photo-settings`, { faces: "aan", beeld: "uit" }],
      ["PUT", `${V}/albums/a1/rule`, { rule: { type: ["image"], person: ["p1"] } }],
      ["PUT", `${V}/albums/a1/rule`, { rule: { q: "strand", country: "NL" }, share_new: false }],
      ["POST", `${V}/albums/a1/rule/confirm`, { share_new: true }],
      ["DELETE", `${V}/albums/a1/rule`],
    ]);
  });

  it("Nu bijwerken: POST /albums/{id}/refresh geeft 202 zonder inhoud, en dat is gewoon gelukt", async () => {
    const request = vi.spyOn(api, "request");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 202 }));
    await expect(people.refreshAlbum("a/1")).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/albums/a%2F1/refresh");
    expect(init.method).toBe("POST");
    expect(request).not.toHaveBeenCalled();
  });

  it("Nu bijwerken: een fout van de server komt als fout terug", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: { code: "not_found", message: "Weg." } }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(people.refreshAlbum("a1")).rejects.toMatchObject({ status: 404, code: "not_found" });
  });

  it("de catalogus people.ts heeft precies de foutcodes van personen, herinneringen en albums", () => {
    expect(Object.keys(peopleCatalog).sort()).toEqual([...PEOPLE_ERROR_CODES].sort());
    for (const { en } of Object.values(peopleCatalog)) expect(en).not.toMatch(/[{}]/);
  });
});
