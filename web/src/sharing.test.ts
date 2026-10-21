import { describe, expect, it } from "vitest";
import { Api, type FileNode, type SearchHit, type Share } from "./api";
import { isGuest } from "./currentUser";
import { affectsFolder, affectsSharedWithMe } from "./events";
import { folderHref, hitPath, parentHref, pathLabel } from "./searchQuery";
import {
  canWrite,
  crumbs,
  dateInputValue,
  endOfDay,
  expiryLabel,
  folderActions,
  formatExpiry,
  isShared,
  linkChange,
  linkHref,
  linkSummary,
  newLink,
  nodeActions,
  permissionOf,
  today,
} from "./sharing";
import type { TusApi } from "./upload";
import { UploadQueue } from "./uploadQueue";

const jonas = { id: "u-jonas", display_name: "Jonas" };

function node(change: Partial<FileNode> = {}): FileNode {
  return {
    id: "n1",
    parent_id: "p1",
    name: "Vakantie",
    kind: "dir",
    mime: null,
    size: 0,
    etag: "e",
    created_at: "2026-10-01T10:00:00+00:00",
    modified_at: "2026-10-01T10:00:00+00:00",
    deleted_at: null,
    tags: [],
    ...change,
  };
}

describe("rechten", () => {
  it("zonder permission (oudere server) is het van jou", () => {
    expect(permissionOf(node())).toBe("owner");
    expect(permissionOf(null)).toBe("owner");
    expect(isShared(node())).toBe(false);
    expect(isShared(node({ permission: "read", owner: jonas }))).toBe(true);
    expect([canWrite("owner"), canWrite("write"), canWrite("read")]).toEqual([true, true, false]);
  });

  it("in een map: uploaden en nieuwe map alleen met schrijven, delen alleen als eigenaar en niet de hoofdmap", () => {
    expect(folderActions(node({ permission: "read" }))).toEqual({ upload: false, mkdir: false, share: false });
    expect(folderActions(node({ permission: "write" }))).toEqual({ upload: true, mkdir: true, share: false });
    expect(folderActions(node({ permission: "owner" }))).toEqual({ upload: true, mkdir: true, share: true });
    expect(folderActions(node({ permission: "owner", parent_id: null }))).toEqual({
      upload: true,
      mkdir: true,
      share: false,
    });
    expect(folderActions(null)).toEqual({ upload: false, mkdir: false, share: false });
  });

  it("alleen kijken: downloaden, kopiëren, tags en versies bekijken; niets veranderen", () => {
    const file = node({ kind: "file", permission: "read" });
    expect(nodeActions(file)).toEqual({
      download: true,
      rename: false,
      trash: false,
      move: false,
      copy: true,
      tags: true,
      versions: true,
      versionWrite: false,
      versionDelete: false,
      share: false,
    });
  });

  it("bewerken: veranderen mag, delen en versies verwijderen niet", () => {
    const a = nodeActions(node({ kind: "file", permission: "write" }));
    expect([a.rename, a.trash, a.move, a.versionWrite]).toEqual([true, true, true, true]);
    expect([a.share, a.versionDelete]).toEqual([false, false]);
  });

  it("de gedeelde map zelf hernoemt, verplaatst of verwijdert alleen de eigenaar", () => {
    const a = nodeActions(node({ permission: "write" }), true);
    expect([a.rename, a.trash, a.move, a.copy]).toEqual([false, false, false, true]);
    const own = nodeActions(node({ permission: "owner" }), true);
    expect([own.rename, own.trash, own.move, own.share]).toEqual([true, true, true, true]);
  });

  it("eigenaar: alles, ook delen en versies verwijderen; een map heeft geen versies", () => {
    const a = nodeActions(node({ kind: "file" }));
    expect(a).toMatchObject({ share: true, versionDelete: true, versionWrite: true, versions: true });
    expect(nodeActions(node()).versions).toBe(false);
    expect(nodeActions(node()).download).toBe(false);
  });

  it("gasten", () => {
    expect(isGuest({ role: "guest" })).toBe(true);
    expect(isGuest({ role: "user" })).toBe(false);
    expect(isGuest(null)).toBe(false);
  });
});

describe("pad boven een map", () => {
  it("je eigen bestanden beginnen bij Mijn bestanden", () => {
    const path = [node({ id: "root", name: "", parent_id: null }), node({ id: "a", name: "Foto's" })];
    expect(crumbs(path)).toEqual([
      { label: "Mijn bestanden", href: "/" },
      { label: "Foto's", href: "/map/a" },
    ]);
  });

  it("een gedeelde map begint bij Gedeeld met mij en dan het pad van de server", () => {
    const path = [
      node({ id: "r", name: "Vakantie", parent_id: null, permission: "read", owner: jonas }),
      node({ id: "b", name: "2026", permission: "read", owner: jonas }),
    ];
    expect(crumbs(path)).toEqual([
      { label: "Gedeeld met mij", href: "/gedeeld" },
      { label: "Vakantie", href: "/map/r" },
      { label: "2026", href: "/map/b" },
    ]);
    expect(crumbs([])).toEqual([]);
  });
});

describe("vervaldatum", () => {
  it("een datumveld wordt het eind van die dag, in de eigen tijdzone", () => {
    const iso = endOfDay("2026-10-31")!;
    const at = new Date(iso);
    expect([at.getFullYear(), at.getMonth(), at.getDate(), at.getHours(), at.getMinutes()]).toEqual([
      2026, 9, 31, 23, 59,
    ]);
    expect(dateInputValue(iso)).toBe("2026-10-31");
    expect(endOfDay("2026-02-31")).toBeNull();
    expect(endOfDay("31-10-2026")).toBeNull();
    expect(endOfDay("")).toBeNull();
    expect(dateInputValue(null)).toBe("");
    expect(dateInputValue("onzin")).toBe("");
    expect(today(new Date(2026, 9, 20, 15, 0).getTime())).toBe("2026-10-20");
  });

  it("verloopt op …, of verlopen", () => {
    const now = new Date(2026, 9, 20, 12, 0).getTime();
    const endOf31 = new Date(2026, 9, 31, 23, 59, 59, 999).toISOString();
    expect(formatExpiry(endOf31)).toBe("31 oktober 2026");
    expect(expiryLabel(endOf31, now)).toBe("verloopt op 31 oktober 2026");
    expect(expiryLabel(new Date(2026, 10, 2, 14, 30).toISOString(), now)).toBe("verloopt op 2 november 2026 om 14:30");
    expect(expiryLabel(new Date(2026, 9, 19).toISOString(), now)).toBe("verlopen");
    expect(expiryLabel(null, now)).toBeNull();
  });
});

const link: Share = {
  id: "s1",
  node_id: "n1",
  type: "link",
  permission: "read",
  created_at: "2026-10-01T10:00:00+00:00",
  expires_at: null,
  user: null,
  group: null,
  link: { url: "/s/abc_DEF-123", label: null, has_password: false },
};

describe("links", () => {
  it("het adres om te versturen is dat van deze app plus /s/<token>", () => {
    expect(linkHref(link, "http://192.168.1.10:8080")).toBe("http://192.168.1.10:8080/s/abc_DEF-123");
    expect(linkHref({ link: null }, "https://x.nl")).toBe("");
  });

  it("samenvatting: soort, wachtwoord en vervaldatum", () => {
    const now = new Date(2026, 9, 20).getTime();
    expect(linkSummary(link, now)).toBe("Alleen bekijken");
    const upload: Share = {
      ...link,
      permission: "upload",
      expires_at: new Date(2026, 9, 31, 23, 59, 59).toISOString(),
      link: { ...link.link!, has_password: true },
    };
    expect(linkSummary(upload, now)).toBe("Alleen uploaden · met wachtwoord · verloopt op 31 oktober 2026");
    expect(linkSummary({ ...link, expires_at: new Date(2026, 9, 1).toISOString() }, now)).toBe(
      "Alleen bekijken · verlopen",
    );
  });

  const form = { permission: "read" as const, date: "", password: "", removePassword: false, label: "" };
  const now = new Date(2026, 9, 20, 12).getTime();

  it("een nieuwe link stuurt alleen wat ingevuld is", () => {
    expect(newLink(form, now)).toEqual({ permission: "read" });
    expect(
      newLink({ ...form, permission: "upload", password: "geheim1", date: "2026-10-31", label: " Familie " }, now),
    ).toEqual({
      permission: "upload",
      password: "geheim1",
      expires_at: endOfDay("2026-10-31"),
      label: "Familie",
    });
    expect(() => newLink({ ...form, password: "kort" }, now)).toThrow("minstens 6 tekens");
    expect(() => newLink({ ...form, password: "x".repeat(201) }, now)).toThrow("hooguit 200 tekens");
    expect(() => newLink({ ...form, date: "2026-10-19" }, now)).toThrow("vanaf vandaag");
    expect(newLink({ ...form, date: "2026-10-20" }, now).expires_at).toBe(endOfDay("2026-10-20")); // vandaag mag
  });

  it("wijzigen stuurt alleen wat veranderde", () => {
    const share: Share = {
      ...link,
      expires_at: endOfDay("2026-10-31"),
      link: { url: "/s/x", label: "Familie", has_password: true },
    };
    const same = { ...form, date: "2026-10-31", label: "Familie" };
    expect(linkChange(share, same, now)).toEqual({});
    expect(linkChange(share, { ...same, date: "" }, now)).toEqual({ expires_at: null });
    expect(linkChange(share, { ...same, date: "2026-11-30" }, now)).toEqual({ expires_at: endOfDay("2026-11-30") });
    expect(linkChange(share, { ...same, removePassword: true }, now)).toEqual({ password: "" });
    expect(linkChange(share, { ...same, password: "nieuw-geheim" }, now)).toEqual({ password: "nieuw-geheim" });
    expect(linkChange(share, { ...same, label: "" }, now)).toEqual({ label: "" });
    // Een verlopen link waarvan je de datum niet aanraakt: geen fout
    const expired = { ...share, expires_at: endOfDay("2026-10-01") };
    expect(linkChange(expired, { ...same, date: "2026-10-01" }, now)).toEqual({});
  });
});

describe("zoeken in wat met je gedeeld is", () => {
  const shared = (n: Partial<FileNode>, path: SearchHit["path"]): SearchHit => ({
    node: node({ permission: "read", owner: jonas, ...n }),
    path,
  });

  it("het pad begint bij de gedeelde map, met Gedeeld met mij ervoor", () => {
    const hit = shared({ id: "f", kind: "file" }, [
      { id: "r", name: "Vakantie" },
      { id: "b", name: "2026" },
    ]);
    expect(pathLabel(hitPath(hit), true)).toBe("Gedeeld met mij / Vakantie / 2026");
    expect(parentHref(hit)).toBe("/map/b");
    expect(folderHref(hit)).toBe("/map/b");
    // Direct in de gedeelde map: niet naar je eigen hoofdmap
    const top = shared({ id: "f", kind: "file" }, [{ id: "r", name: "Vakantie" }]);
    expect(parentHref(top)).toBe("/map/r");
  });

  it("de gedeelde map zelf gevonden: wat erboven staat zie je niet", () => {
    const self = shared({ id: "r", kind: "dir" }, [{ id: "r", name: "Vakantie" }]);
    expect(hitPath(self)).toEqual([]);
    expect(pathLabel(hitPath(self), true)).toBe("Gedeeld met mij");
    expect(folderHref(self)).toBe("/map/r");
    const file = shared({ id: "f", kind: "file", name: "x.pdf" }, []);
    expect(parentHref(file)).toBe("/gedeeld");
  });
});

describe("live gebeurtenissen bij delen", () => {
  const event = (type: string, data: Record<string, unknown> = {}) => ({ type, data });

  it("een gedeelde map laadt opnieuw als een deling verandert, je eigen map niet", () => {
    const changed = event("share.changed", { share_id: "s", node_id: "elders" });
    expect(affectsFolder(changed, "map", true)).toBe(true);
    expect(affectsFolder(changed, "map", false)).toBe(false);
    expect(affectsFolder(event("share.deleted", { share_id: "s", node_id: "x" }), "map", true)).toBe(true);
  });

  it("Gedeeld met mij laadt opnieuw bij delingen en bij wijzigingen van wat er staat", () => {
    const ids = new Set(["r1"]);
    expect(affectsSharedWithMe(event("share.created", { share_id: "s", node_id: "r2" }), ids)).toBe(true);
    expect(affectsSharedWithMe(event("connected"), ids)).toBe(true);
    expect(affectsSharedWithMe(event("node.changed", { id: "r1", parent_id: null }), ids)).toBe(true);
    expect(affectsSharedWithMe(event("node.created", { id: "x", parent_id: "r1" }), ids)).toBe(false);
    expect(affectsSharedWithMe(event("preview.ready", { node_ids: ["r1"] }), ids)).toBe(false);
  });

  it("Gedeeld met mij: een gedeelde map die terugkomt uit de prullenbak (nog niet in de lijst)", () => {
    const ids = new Set(["r1"]);
    // Zonder parent_id: het gaat over een gedeelde map zelf
    expect(affectsSharedWithMe(event("node.changed", { id: "r2", parent_id: null }), ids)).toBe(true);
    expect(affectsSharedWithMe(event("node.changed", { id: "r2", parent_id: "p" }), ids)).toBe(false);
  });

  it("een map laadt opnieuw als een map erboven verandert of weggaat", () => {
    // Gijs staat in Gedeeld/Project/Sub; Jonas gooit Project (de gedeelde map) weg of hernoemt hem
    const ancestors = ["project", "sub"];
    const deleted = event("node.deleted", { id: "project", parent_id: null });
    expect(affectsFolder(deleted, "sub", true, ancestors)).toBe(true);
    expect(affectsFolder(deleted, "sub", true)).toBe(false);
    expect(affectsFolder(event("node.changed", { id: "project", parent_id: null }), "sub", true, ancestors)).toBe(true);
    // Iets ernaast, of iets nieuws in een map erboven: niet
    expect(affectsFolder(event("node.changed", { id: "ander", parent_id: "project" }), "sub", true, ancestors)).toBe(
      false,
    );
    expect(affectsFolder(event("node.created", { id: "x", parent_id: "project" }), "sub", true, ancestors)).toBe(false);
  });
});

interface Seen {
  method: string;
  url: string;
  body: unknown;
}

function recording(reply: unknown = {}, status = 200) {
  const seen: Seen[] = [];
  const api = new Api(async (input, init) => {
    const body = init?.body;
    seen.push({
      method: init?.method ?? "GET",
      url: String(input),
      body: typeof body === "string" ? JSON.parse(body) : body,
    });
    return status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(reply), { status, headers: { "Content-Type": "application/json" } });
  });
  return { api, seen };
}

describe("Api: delen", () => {
  it("zoeken naar mensen, delen, wijzigen en intrekken", async () => {
    const { api, seen } = recording({});
    await api.directory("jo & co");
    await api.shares("n1");
    await api.share("n1", { user_id: "u2" }, "read");
    await api.share("n1", { group_id: "g1" }, "write", "2026-10-31T22:59:59.999Z");
    await api.changeShare("s1", { permission: "write" });
    await api.changeShare("s2", { expires_at: null, password: "" });
    await api.sharedWithMe();
    expect(seen.map((s) => [s.method, s.url, s.body])).toEqual([
      ["GET", "/api/v1/directory?q=jo+%26+co", undefined],
      ["GET", "/api/v1/files/n1/shares", undefined],
      ["POST", "/api/v1/files/n1/shares", { user_id: "u2", permission: "read" }],
      [
        "POST",
        "/api/v1/files/n1/shares",
        { group_id: "g1", permission: "write", expires_at: "2026-10-31T22:59:59.999Z" },
      ],
      ["PATCH", "/api/v1/shares/s1", { permission: "write" }],
      ["PATCH", "/api/v1/shares/s2", { expires_at: null, password: "" }],
      ["GET", "/api/v1/shared-with-me", undefined],
    ]);
  });

  it("links maken en verwijderen", async () => {
    const { api, seen } = recording(link);
    await api.createLink("n1", { permission: "upload", password: "geheim1" });
    const deleted = recording(null, 204);
    await expect(deleted.api.deleteShare("s1")).resolves.toBeUndefined();
    expect(seen[0]).toEqual({
      method: "POST",
      url: "/api/v1/files/n1/links",
      body: { permission: "upload", password: "geheim1" },
    });
    expect(deleted.seen[0]).toMatchObject({ method: "DELETE", url: "/api/v1/shares/s1" });
  });

  it("groepen beheren", async () => {
    const { api, seen } = recording({});
    await api.groups();
    await api.createGroup("Familie");
    await api.renameGroup("g1", "Gezin");
    await api.setGroupMembers("g1", ["u1", "u2"]);
    expect(seen.map((s) => [s.method, s.url, s.body])).toEqual([
      ["GET", "/api/v1/groups", undefined],
      ["POST", "/api/v1/groups", { name: "Familie" }],
      ["PATCH", "/api/v1/groups/g1", { name: "Gezin" }],
      ["PUT", "/api/v1/groups/g1/members", { user_ids: ["u1", "u2"] }],
    ]);
  });

  it("de melding en code van de server komen door (al gedeeld, hoofdmap, alleen kijken)", async () => {
    const taken = recording(
      { detail: { message: "Al gedeeld met deze persoon of groep", code: "already_shared" } },
      409,
    );
    await expect(taken.api.share("n1", { user_id: "u2" }, "read")).rejects.toMatchObject({
      status: 409,
      code: "already_shared",
      message: "Al gedeeld met deze persoon of groep",
    });
    const readOnly = recording({ detail: { message: "Je mag hier alleen kijken", code: "read_only" } }, 403);
    await expect(readOnly.api.mkdir("m1", "x")).rejects.toMatchObject({ status: 403, code: "read_only" });
    const cross = recording(
      {
        detail: {
          message: "Verplaatsen kan alleen binnen dezelfde eigenaar; kopieer het in plaats daarvan",
          code: "cross_owner",
        },
      },
      422,
    );
    await expect(cross.api.change("n1", { parent_id: "m2" })).rejects.toMatchObject({ code: "cross_owner" });
  });
});

describe("UploadQueue meldt of een upload lukte", () => {
  it("ok en het bestand, ook bij mislukken", async () => {
    let fail = false;
    const api: TusApi = {
      accessToken: "k",
      refresh: async () => true,
      tusCreate: async () => {
        if (fail) throw new Error("Onvoldoende quotum");
        return { url: "/u/1", nodeId: "n" }; // leeg bestand: meteen klaar
      },
      tusStatus: async () => ({ offset: 0, nodeId: null }),
      tusCancel: async () => {},
    };
    const queue = new UploadQueue(api, { storage: { getItem: () => null, setItem: () => {}, removeItem: () => {} } });
    const finished: [string, boolean, string][] = [];
    queue.onFinished((parent, ok, file) => finished.push([parent, ok, file.name]));
    await queue.add("p", [new File([], "a.txt")]);
    fail = true;
    await queue.add("p", [new File([], "b.txt")]);
    expect(finished).toEqual([
      ["p", true, "a.txt"],
      ["p", false, "b.txt"],
    ]);
  });
});
