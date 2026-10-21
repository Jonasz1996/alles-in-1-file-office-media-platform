import { describe, expect, it } from "vitest";
import { Api, type SearchHit, searchParams } from "./api";
import { folderHref, hasFilter, parentHref, pathLabel, readSearch, searchHref } from "./searchQuery";
import { tagStyle } from "./tagColors";

interface Seen {
  url: string;
  method: string;
  body: unknown;
}

/** Api met een nep-fetch die elk verzoek onthoudt en `reply` teruggeeft. */
function recording(reply: unknown = {}, status = 200) {
  const seen: Seen[] = [];
  const api = new Api(async (input, init) => {
    const body = init?.body;
    seen.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: typeof body === "string" ? JSON.parse(body) : body,
    });
    return status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(reply), { status, headers: { "Content-Type": "application/json" } });
  });
  return { api, seen };
}

describe("zoeken", () => {
  it("laat lege velden weg en codeert de rest", () => {
    expect(searchParams({ q: " foto & co ", type: "", tag: undefined, limit: 50 })).toBe("q=foto+%26+co&limit=50");
    expect(searchParams({})).toBe("");
  });

  it("leest de adresbalk en negeert onzin", () => {
    expect(readSearch("?q=vakantie&type=image&tag=t1&in=m1&limit=500")).toEqual({
      q: "vakantie",
      type: "image",
      tag: "t1",
      in: "m1",
      limit: 200,
    });
    expect(readSearch("?q=%20%20&type=foto&limit=-3")).toEqual({});
    expect(hasFilter(readSearch("?limit=10"))).toBe(false);
    expect(hasFilter(readSearch("?tag=x"))).toBe(true);
  });

  it("heen en terug via de adresbalk", () => {
    const query = { q: "café menu", type: "pdf" as const };
    expect(searchHref(query)).toBe("/zoeken?q=caf%C3%A9+menu&type=pdf");
    expect(readSearch(searchHref(query).slice("/zoeken".length))).toEqual(query);
    expect(searchHref({})).toBe("/zoeken");
  });

  it("pad en waar een klik heen gaat", () => {
    const file: SearchHit = {
      node: { id: "f", kind: "file" } as SearchHit["node"],
      path: [
        { id: "root", name: "" },
        { id: "a", name: "Foto's" },
        { id: "b", name: "2026" },
      ],
    };
    expect(pathLabel(file.path)).toBe("Mijn bestanden / Foto's / 2026");
    expect(parentHref(file)).toBe("/map/b");
    expect(folderHref(file)).toBe("/map/b");
    const inRoot = { ...file, path: [{ id: "root", name: "" }] };
    expect(folderHref(inRoot)).toBe("/");
    const folder = { ...inRoot, node: { id: "d", kind: "dir" } as SearchHit["node"] };
    expect(folderHref(folder)).toBe("/map/d");
  });

  it("vraagt de server met de juiste parameters", async () => {
    const { api, seen } = recording({ results: [], more: false });
    await api.search({ q: "100%", tag: "t1" });
    expect(seen[0].url).toBe("/api/v1/search?q=100%25&tag=t1");
  });
});

describe("kopiëren, versies en tags", () => {
  it("kopiëren stuurt het doel en eventueel een naam", async () => {
    const { api, seen } = recording({ id: "k" });
    await api.copy("n1", "m1");
    await api.copy("n1", "m1", "kopie.txt");
    expect(seen.map((s) => [s.method, s.url, s.body])).toEqual([
      ["POST", "/api/v1/files/n1/copy", { parent_id: "m1" }],
      ["POST", "/api/v1/files/n1/copy", { parent_id: "m1", name: "kopie.txt" }],
    ]);
  });

  it("versies: lijst, terugzetten, verwijderen en downloadlink", async () => {
    const { api, seen } = recording({ url: "/api/v1/download/n1?token=x" });
    await api.versions("n1");
    await api.restoreVersion("n1", "v1");
    expect(await api.versionDownloadUrl("n1", "v1")).toBe("/api/v1/download/n1?token=x");
    expect(seen.map((s) => `${s.method} ${s.url}`)).toEqual([
      "GET /api/v1/files/n1/versions",
      "POST /api/v1/files/n1/versions/v1/restore",
      "POST /api/v1/files/n1/download-link?version=v1",
    ]);
    const deleted = recording(null, 204);
    await expect(deleted.api.deleteVersion("n1", "v1")).resolves.toBeUndefined();
    expect(deleted.seen[0]).toMatchObject({ method: "DELETE", url: "/api/v1/files/n1/versions/v1" });
  });

  it("een nieuwe versie uploaden vervangt de inhoud van het bestand zelf", async () => {
    const { api, seen } = recording({ id: "n1" });
    const file = new Blob(["nieuw"]);
    await api.replaceContent("n1", file);
    expect(seen[0].method).toBe("PUT");
    // Via het bestand, niet via de map erboven: die ziet wie alleen dit bestand gedeeld kreeg niet
    expect(seen[0].url).toBe("/api/v1/files/n1/content");
    expect(seen[0].body).toBe(file);
  });

  it("tags maken, wijzigen, verwijderen en zetten", async () => {
    const { api, seen } = recording([]);
    await api.tags();
    await api.createTag("Werk", "#1d5fd1");
    await api.changeTag("t1", { name: "Thuis" });
    await api.setTags("n1", ["t1", "t2"]);
    expect(seen.map((s) => [s.method, s.url, s.body])).toEqual([
      ["GET", "/api/v1/tags", undefined],
      ["POST", "/api/v1/tags", { name: "Werk", color: "#1d5fd1" }],
      ["PATCH", "/api/v1/tags/t1", { name: "Thuis" }],
      ["PUT", "/api/v1/files/n1/tags", { tag_ids: ["t1", "t2"] }],
    ]);
  });

  it("een bezette tagnaam geeft de code van de server door", async () => {
    const { api } = recording({ detail: { message: "Je hebt al een tag met deze naam", code: "name_taken" } }, 409);
    await expect(api.createTag("werk")).rejects.toMatchObject({ status: 409, code: "name_taken" });
  });

  it("alleen een geldige kleur komt in de stijl", () => {
    expect(tagStyle("#1D5FD1")).toEqual({ "--tag": "#1D5FD1" });
    expect(tagStyle("")).toBeUndefined();
    expect(tagStyle("red; background: url(x)")).toBeUndefined();
  });
});
