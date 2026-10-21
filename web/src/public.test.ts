import { describe, expect, it } from "vitest";
import { ApiError, type PublicShare } from "./api";
import { ARRIVED, keyName, linkProblem, linkToken, PublicLink } from "./publicApi";
import { type SendChunk, uploadFile } from "./upload";
import { UploadQueue } from "./uploadQueue";

const TOKEN = "Tk_abc-123";

const share: PublicShare = {
  kind: "dir",
  name: "Vakantie",
  permission: "upload",
  owner_name: "Jonas",
  expires_at: null,
  root: {
    id: "root-1",
    name: "Vakantie",
    kind: "dir",
    mime: null,
    size: 0,
    etag: "e",
    modified_at: "2026-10-01T10:00:00Z",
  },
};

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

/**
 * Nep-server voor de publieke API: elke keer openen geeft een nieuwe sleutel; alleen de
 * laatste werkt (zoals na een nieuw wachtwoord of als de oude verlopen is).
 */
class FakePublic {
  password: string | null = null;
  keys = 0;
  valid = new Set<string>();
  calls: string[] = [];
  bodies: unknown[] = [];
  expiresIn = 12 * 3600;
  status: "ok" | "gone" | "expired" | "busy" = "ok";
  uploads = new Map<string, { length: number; offset: number }>();
  created = 0;

  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const auth = new Headers(init?.headers).get("Authorization");
    this.calls.push(`${method} ${url} ${auth ?? "-"}`);
    if (url === "/api/v1/public/open") {
      const body = JSON.parse(String(init?.body)) as { token: string; password?: string };
      this.bodies.push(body);
      if (this.status === "busy") return json({ detail: "Te veel pogingen, probeer het straks opnieuw" }, 429);
      if (this.status === "gone" || body.token !== TOKEN)
        return json({ detail: { message: "Deze link bestaat niet (meer)", code: "link_not_found" } }, 404);
      if (this.status === "expired")
        return json({ detail: { message: "Deze link is verlopen", code: "link_expired" } }, 410);
      if (this.password !== null && body.password === undefined)
        return json({ detail: { message: "Voor deze link is een wachtwoord nodig", code: "password_required" } }, 401);
      if (this.password !== null && body.password !== this.password)
        return json({ detail: { message: "Wachtwoord klopt niet", code: "wrong_password" } }, 401);
      const key = `K${++this.keys}`;
      this.valid = new Set([key]);
      return json({ key, expires_in: this.expiresIn, share });
    }
    if (!auth || !this.valid.has(auth.slice("Bearer ".length))) return json({ detail: "Sleutel verlopen" }, 401);
    if (url.startsWith("/api/v1/public/children")) {
      return json({ folder: share.root, path: [share.root], children: [] });
    }
    if (url === "/api/v1/public/previews") {
      this.bodies.push(JSON.parse(String(init?.body)));
      return json({});
    }
    if (url === "/api/v1/public/tus" && method === "POST") {
      const headers = new Headers(init?.headers);
      const id = `/api/v1/public/tus/${++this.created}`;
      this.uploads.set(id, { length: Number(headers.get("Upload-Length")), offset: 0 });
      this.bodies.push(headers.get("Upload-Metadata"));
      return new Response(null, { status: 201, headers: { Location: id, "Tus-Resumable": "1.0.0" } });
    }
    const upload = this.uploads.get(url);
    if (!upload) return new Response(null, { status: 404 });
    if (method === "HEAD") {
      return new Response(null, {
        status: 200,
        headers: { "Upload-Offset": String(upload.offset), "Upload-Length": String(upload.length) },
      });
    }
    if (method === "DELETE") {
      this.uploads.delete(url);
      return new Response(null, { status: 204 });
    }
    return new Response(null, { status: 405 });
  };

  /** PATCH zoals xhrSend: geen Platform-Node-Id, ook niet als het bestand klaar is. */
  send: SendChunk = async (url, offset, chunk, token, onProgress) => {
    this.calls.push(`PATCH ${url} Bearer ${token}`);
    if (!token || !this.valid.has(token)) throw new ApiError(401, "Sleutel verlopen");
    const upload = this.uploads.get(url);
    if (!upload) throw new ApiError(404, "Upload niet gevonden");
    if (offset !== upload.offset) throw new ApiError(409, "Upload-Offset klopt niet");
    upload.offset += chunk.size;
    onProgress(chunk.size);
    return { offset: upload.offset, nodeId: null };
  };
}

function setup(options: { storage?: ReturnType<typeof memoryStorage>; now?: () => number } = {}) {
  const server = new FakePublic();
  const storage = options.storage ?? memoryStorage();
  const link = new PublicLink(TOKEN, { fetch: server.fetch, storage, send: server.send, now: options.now });
  return { server, storage, link };
}

describe("het token uit het adres", () => {
  it("alleen /s/<token>", () => {
    expect(linkToken("/s/Tk_abc-123")).toBe("Tk_abc-123");
    expect(linkToken("/s/Tk_abc-123/")).toBe("Tk_abc-123");
    expect(linkToken("/s/")).toBeNull();
    expect(linkToken("/s/a/b")).toBeNull();
    expect(linkToken("/s/a%20b")).toBeNull();
  });
});

describe("PublicLink openen", () => {
  it("opent met het token in de body en bewaart de sleutel per token", async () => {
    const { server, storage, link } = setup();
    expect(await link.open()).toEqual(share);
    expect(server.bodies[0]).toEqual({ token: TOKEN });
    expect(link.accessToken).toBe("K1");
    expect(JSON.parse(storage.data.get(keyName(TOKEN))!)).toMatchObject({ key: "K1", share });
    // Het token komt nooit in een adres
    await link.children();
    expect(server.calls.filter((c) => c.includes(TOKEN))).toEqual([]);
    expect(server.calls.some((c) => c.includes("/auth/"))).toBe(false);
  });

  it("na herladen (zelfde tabblad) is er meteen een sleutel: niet opnieuw om het wachtwoord vragen", async () => {
    const storage = memoryStorage();
    const first = setup({ storage });
    first.server.password = "geheim1";
    await first.link.open("geheim1");
    const again = new PublicLink(TOKEN, { fetch: first.server.fetch, storage, send: first.server.send });
    expect(again.share).toEqual(share);
    expect(again.accessToken).toBe("K1");
    await again.children();
    expect(first.server.keys).toBe(1);
  });

  it("een opgeslagen sleutel die (bijna) verlopen is, telt niet", () => {
    const storage = memoryStorage();
    storage.setItem(keyName(TOKEN), JSON.stringify({ key: "oud", renewAt: 1000, share }));
    const link = new PublicLink(TOKEN, { storage, now: () => 2000 });
    expect(link.share).toBeNull();
    expect(link.accessToken).toBeNull();
    storage.setItem(keyName(TOKEN), "kapot");
    expect(new PublicLink(TOKEN, { storage }).share).toBeNull();
  });

  it("wachtwoord nodig, fout wachtwoord, dan goed", async () => {
    const { server, link } = setup();
    server.password = "geheim1";
    const needed = await link.open().catch((e) => e);
    expect(linkProblem(needed)).toEqual({ kind: "password", message: null });
    const wrong = await link.open("fout").catch((e) => e);
    expect(linkProblem(wrong)).toEqual({ kind: "password", message: "Wachtwoord klopt niet" });
    await link.open("geheim1");
    expect(link.accessToken).toBe("K1");
  });

  it("weg, verlopen of te vaak: een duidelijke melding", async () => {
    for (const [status, problem] of [
      ["gone", { kind: "gone", message: "Deze link bestaat niet (meer)" }],
      ["expired", { kind: "gone", message: "Deze link is verlopen" }],
      ["busy", { kind: "busy", message: "Te veel pogingen, probeer het straks opnieuw" }],
    ] as const) {
      const { server, link } = setup();
      server.status = status;
      expect(linkProblem(await link.open().catch((e) => e))).toEqual(problem);
    }
    expect(linkProblem(new ApiError(0, "Netwerkfout")).kind).toBe("error");
    expect(linkProblem(new TypeError("Failed to fetch"))).toEqual({
      kind: "error",
      message: "Geen verbinding met de server. Probeer het opnieuw.", // fetch zelf mislukte
    });
  });

  it("een link die weg is, haalt ook de bewaarde sleutel weg; te vaak (429) niet", async () => {
    const { server, storage, link } = setup();
    await link.open();
    server.status = "busy";
    await link.open().catch(() => {});
    expect(storage.data.has(keyName(TOKEN))).toBe(true);
    server.status = "gone";
    await link.open().catch(() => {});
    expect(storage.data.has(keyName(TOKEN))).toBe(false);
    expect(link.share).toBeNull();
  });
});

describe("PublicLink: sleutel verlopen", () => {
  it("een 401 later: opnieuw openen (met het wachtwoord van eerder) en nog één keer", async () => {
    const { server, link } = setup();
    server.password = "geheim1";
    await link.open("geheim1");
    server.valid.clear(); // de sleutel is verlopen
    await link.children("sub");
    expect(server.bodies.at(-1)).toEqual({ token: TOKEN, password: "geheim1" });
    expect(server.calls.filter((c) => c.startsWith("GET"))).toEqual([
      "GET /api/v1/public/children?folder=sub Bearer K1",
      "GET /api/v1/public/children?folder=sub Bearer K2",
    ]);
  });

  it("veranderde het wachtwoord, dan opnieuw vragen (en de pagina krijgt het te horen)", async () => {
    const { server, link } = setup();
    server.password = "geheim1";
    await link.open("geheim1");
    const locked: ApiError[] = [];
    link.onLocked = (e) => locked.push(e);
    server.password = "anders1";
    server.valid.clear();
    const error = await link.children().catch((e) => e);
    expect(error).toMatchObject({ status: 401, code: "password_required" });
    expect(locked.map((e) => e.code)).toEqual(["password_required"]);
    expect(linkProblem(error)).toEqual({ kind: "password", message: null });
  });

  it("de link is intussen weg: de pagina krijgt het te horen", async () => {
    const { server, link } = setup();
    await link.open();
    const locked: string[] = [];
    link.onLocked = (e) => locked.push(e.code ?? "");
    server.status = "gone";
    server.valid.clear();
    await expect(link.previews(["a"])).rejects.toMatchObject({ code: "link_not_found" });
    expect(locked).toEqual(["link_not_found"]);
  });

  it("vlak voor het verlopen al een nieuwe sleutel; gelijktijdige verzoeken openen één keer", async () => {
    let now = 1_000_000;
    const { server, link } = setup({ now: () => now });
    server.expiresIn = 600;
    await link.open();
    now += 600_000 - 30_000; // nog 30 s
    await Promise.all([link.children(), link.downloadUrl("n1"), link.previews(["n1"])]);
    expect(server.keys).toBe(2);
  });

  it("een sleutel die maar kort geldt (de link verloopt bijna), niet bij elk verzoek vernieuwen", async () => {
    let now = 1_000_000;
    const { server, link } = setup({ now: () => now });
    server.expiresIn = 40;
    await link.open();
    now += 10_000;
    await link.children();
    expect(server.keys).toBe(1);
    now += 15_000; // over de helft
    await link.children();
    expect(server.keys).toBe(2);
  });
});

describe("PublicLink: adressen", () => {
  it("download met de sleutel in het adres, inline om te tonen", async () => {
    const { link } = setup();
    await link.open();
    expect(await link.downloadUrl("n1")).toBe("/api/v1/public/download/n1?key=K1");
    expect(await link.downloadUrl("n1", true)).toBe("/api/v1/public/download/n1?key=K1&inline=true");
  });

  it("previews en submappen", async () => {
    const { server, link } = setup();
    await link.open();
    await link.previews(["a", "b"]);
    await link.children();
    expect(server.bodies.at(-1)).toEqual({ node_ids: ["a", "b"] });
    expect(server.calls.at(-1)).toBe("GET /api/v1/public/children Bearer K1");
  });
});

const file = (size: number, name = "foto.jpg") => new File([new Uint8Array(size)], name, { lastModified: 1 });

describe("uploaden via een link (tus)", () => {
  it("alleen de bestandsnaam in de metadata; klaar als alle bytes binnen zijn", async () => {
    const { server, link } = setup();
    await link.open();
    const progress: number[] = [];
    const storage = memoryStorage();
    const result = await uploadFile(link, "public:root-1", file(25), (f) => progress.push(f), {
      send: link.send,
      storage,
      chunkSize: 10,
    });
    expect(result).toBe(ARRIVED);
    expect(server.bodies.at(-1)).toBe("filename Zm90by5qcGc="); // geen parent of conflict
    expect(server.uploads.get("/api/v1/public/tus/1")).toEqual({ length: 25, offset: 25 });
    expect(progress.at(-1)).toBe(1);
    expect(storage.data.size).toBe(0);
    expect(server.calls.filter((c) => c.startsWith("PATCH")).every((c) => c.endsWith("Bearer K1"))).toBe(true);
  });

  it("een leeg bestand: de PATCH van 0 bytes bevestigt het", async () => {
    const { link } = setup();
    await link.open();
    expect(
      await uploadFile(link, "public:root-1", file(0), () => {}, { send: link.send, storage: memoryStorage() }),
    ).toBe(ARRIVED);
  });

  it("sleutel verlopen midden in een upload: opnieuw openen en verder", async () => {
    const { server, link } = setup();
    server.password = "geheim1";
    await link.open("geheim1");
    let chunks = 0;
    const send: SendChunk = async (...args) => {
      if (++chunks === 2) server.valid.clear(); // na het eerste stuk verloopt de sleutel
      return link.send(...args);
    };
    const result = await uploadFile(link, "public:root-1", file(30), () => {}, {
      send,
      storage: memoryStorage(),
      chunkSize: 10,
    });
    expect(result).toBe(ARRIVED);
    expect(server.keys).toBe(2);
    expect(server.uploads.get("/api/v1/public/tus/1")?.offset).toBe(30);
    expect(server.created).toBe(1);
  });

  it("na herladen verder met dezelfde upload (de lengte komt dan uit de HEAD)", async () => {
    const storage = memoryStorage();
    const first = setup();
    await first.link.open();
    let chunks = 0;
    const dies: SendChunk = async (...args) => {
      if (++chunks > 1) throw new ApiError(0, "Netwerkfout");
      return first.link.send(...args);
    };
    await expect(
      uploadFile(first.link, "public:root-1", file(30), () => {}, {
        send: dies,
        storage,
        sleep: async () => {},
        chunkSize: 10,
      }),
    ).rejects.toThrow("gaat verder waar hij was");

    // Een nieuwe pagina: nieuwe PublicLink, die de lengte van de upload niet kent
    const again = new PublicLink(TOKEN, {
      fetch: first.server.fetch,
      storage: memoryStorage(),
      send: first.server.send,
    });
    await again.open();
    const result = await uploadFile(again, "public:root-1", file(30), () => {}, {
      send: again.send,
      storage,
      chunkSize: 10,
    });
    expect(result).toBe(ARRIVED);
    expect(first.server.created).toBe(1);
    expect(first.server.uploads.get("/api/v1/public/tus/1")?.offset).toBe(30);
  });

  it("annuleren gooit de upload weg met de sleutel", async () => {
    const { server, link } = setup();
    await link.open();
    const controller = new AbortController();
    const send: SendChunk = async (...args) => {
      const result = await link.send(...args);
      controller.abort();
      return result;
    };
    await expect(
      uploadFile(link, "public:root-1", file(30), () => {}, {
        send,
        storage: memoryStorage(),
        chunkSize: 10,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "aborted" });
    expect(server.uploads.size).toBe(0);
    expect(server.calls).toContain("DELETE /api/v1/public/tus/1 Bearer K1");
  });

  it("HEAD zonder body: 413 is het quotum van de eigenaar", async () => {
    const full = new PublicLink(TOKEN, {
      storage: null,
      fetch: async (input) =>
        String(input) === "/api/v1/public/open"
          ? json({ key: "K", expires_in: 3600, share })
          : new Response(null, { status: 413 }),
    });
    await full.open();
    await expect(full.tusStatus("/api/v1/public/tus/1")).rejects.toMatchObject({
      status: 413,
      message: "Onvoldoende quotum",
    });
  });

  it("de wachtrij: per bestand of het aankwam", async () => {
    const { link } = setup();
    await link.open();
    const queue = new UploadQueue(link, { send: link.send, storage: memoryStorage(), chunkSize: 10 });
    const arrived: string[] = [];
    queue.onFinished((_parent, ok, f) => ok && arrived.push(f.name));
    await queue.add("public:root-1", [file(15, "a.jpg"), file(5, "b.jpg")]);
    expect(arrived).toEqual(["a.jpg", "b.jpg"]);
    expect(queue.getSnapshot()).toEqual([]);
  });
});
