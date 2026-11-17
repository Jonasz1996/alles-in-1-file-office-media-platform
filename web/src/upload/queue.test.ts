import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { loadAreas } from "../i18n";
import type { SendChunk, TusApi } from "../upload";
import { UploadQueue } from "../uploadQueue";
import type { TreeResult, UploadRequests } from "./api";
import type { ConflictAnswer, ConflictItem } from "./conflicts";

/** Nep-tus-server: houdt per upload bij wat er binnen is, en wat er bij het aanmaken meekwam. */
class Server implements TusApi {
  accessToken = "A";
  received = new Map<string, number>();
  sizes = new Map<string, number>();
  done = new Map<string, string>();
  metas: { parent: string; name: string; meta?: Record<string, string> }[] = [];
  cancelled: string[] = [];
  created = 0;
  async refresh() {
    return {};
  }
  async tusCreate(parent: string, name: string, size: number, meta?: Record<string, string>) {
    const url = `/u/${++this.created}`;
    this.metas.push({ parent, name, meta });
    this.received.set(url, 0);
    this.sizes.set(url, size);
    if (size === 0) {
      this.done.set(url, `node-${url}`);
      return { url, nodeId: `node-${url}` };
    }
    return { url, nodeId: null };
  }
  async tusStatus(url: string) {
    if (!this.received.has(url)) throw new ApiError(404, "weg");
    return { offset: this.received.get(url) ?? 0, nodeId: this.done.get(url) ?? null };
  }
  async tusCancel(url: string) {
    this.cancelled.push(url);
    this.received.delete(url);
  }
  send: SendChunk = async (url, offset, chunk, _token, onProgress, signal) => {
    if (signal?.aborted) throw new ApiError(0, "Geannuleerd", "aborted");
    const have = this.received.get(url);
    if (have === undefined) throw new ApiError(404, "weg");
    if (offset !== have) throw new ApiError(409, "offset");
    const now = have + chunk.size;
    this.received.set(url, now);
    onProgress(chunk.size);
    if (now === this.sizes.get(url)) this.done.set(url, `node-${url}`);
    return { offset: now, nodeId: this.done.get(url) ?? null };
  };
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

const file = (name: string, size: number, lastModified = 1) =>
  new File([new Uint8Array(size)], name, { lastModified });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Een deur per upload-URL: die stuurt pas als de test hem opent. */
function doors() {
  const waiting = new Map<string, () => void>();
  const opened = new Set<string>();
  return {
    async wait(url: string, signal?: AbortSignal) {
      if (opened.has(url)) return;
      await new Promise<void>((resolve) => {
        waiting.set(url, resolve);
        signal?.addEventListener("abort", () => resolve(), { once: true });
      });
    },
    open(url: string) {
      opened.add(url);
      waiting.get(url)?.();
    },
    get waiting() {
      return [...waiting.keys()].filter((u) => !opened.has(u));
    },
  };
}

beforeAll(async () => {
  await loadAreas(["upload"]);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UploadQueue: rij en totalen", () => {
  it("5.000 bestanden in de rij is O(n): snel toevoegen en alles komt aan", async () => {
    const server = new Server();
    const queue = new UploadQueue(server, { send: server.send, storage: memoryStorage(), concurrency: 3 });
    let notified = 0;
    queue.subscribe(() => notified++);
    const files = Array.from({ length: 5000 }, (_, i) => file(`f${i}.txt`, 1 + (i % 7)));
    const start = performance.now();
    const done = queue.add("p", files);
    const added = performance.now() - start;
    expect(queue.getSnapshot()).toHaveLength(5000);
    expect(queue.getSummary()).toMatchObject({ files: 5000, done: 0 });
    // Eén melding voor de hele batch (plus de eerste starts), geen 5.000
    expect(notified).toBeLessThan(10);
    expect(added).toBeLessThan(250);
    await done;
    expect(performance.now() - start).toBeLessThan(5000);
    expect(server.done.size).toBe(5000);
    expect(queue.getSnapshot()).toEqual([]);
    // Na afloop beginnen de totalen opnieuw
    expect(queue.getSummary()).toMatchObject({ files: 0, done: 0, bytes: 0 });
  });

  it("dubbele bestanden in één batch komen er één keer in (op storageKey)", async () => {
    const server = new Server();
    const queue = new UploadQueue(server, { send: server.send, storage: memoryStorage() });
    const a = file("a.txt", 3);
    await queue.add("p", [a, a, file("a.txt", 3), file("a.txt", 3, 2)]);
    expect(server.created).toBe(2); // andere datum = ander bestand
  });

  it("de totalen lopen mee: bestanden, bytes en verzonden", async () => {
    const server = new Server();
    const gate = doors();
    const send: SendChunk = async (url, ...rest) => {
      await gate.wait(url, rest[4]);
      return server.send(url, ...rest);
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage(), concurrency: 2 });
    const done = queue.add("p", [file("a", 10), file("b", 30), file("c", 60)]);
    await tick();
    expect(queue.getSummary()).toMatchObject({ files: 3, bytes: 100, sent: 0, running: 2, waiting: 1 });
    gate.open("/u/1");
    await tick();
    await tick();
    expect(queue.getSummary()).toMatchObject({ done: 1, sent: 10, running: 2, waiting: 0 });
    gate.open("/u/2");
    gate.open("/u/3");
    await done;
    expect(server.done.size).toBe(3);
  });

  it("start er hooguit concurrency tegelijk, en maar één naast een groot bestand", async () => {
    const server = new Server();
    const gate = doors();
    let busy = 0;
    let most = 0;
    const send: SendChunk = async (url, ...rest) => {
      busy++;
      most = Math.max(most, busy);
      await gate.wait(url, rest[4]);
      busy--;
      return server.send(url, ...rest);
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage(), concurrency: 3, bigFile: 50 });
    const done = queue.add("p", [file("a", 5), file("b", 5), file("c", 5), file("d", 5), file("groot", 60)]);
    await tick();
    expect(queue.getSummary().running).toBe(3);
    for (const url of ["/u/1", "/u/2", "/u/3"]) gate.open(url);
    await tick();
    await tick();
    // d mag, het grote bestand wacht tot hij alleen is
    expect(queue.getSnapshot().map((i) => [i.file.name, i.state])).toEqual([
      ["d", "running"],
      ["groot", "waiting"],
    ]);
    gate.open("/u/4");
    await tick();
    await tick();
    expect(queue.getSnapshot().map((i) => [i.file.name, i.state])).toEqual([["groot", "running"]]);
    gate.open("/u/5");
    await done;
    expect(most).toBe(3);
  });

  it("meldt de samenvatting hooguit 4 keer per seconde", async () => {
    vi.useFakeTimers();
    let now = 0;
    const server = new Server();
    const queue = new UploadQueue(server, { send: server.send, storage: memoryStorage(), now: () => now });
    let summaries = 0;
    let changes = 0;
    queue.subscribeSummary(() => summaries++);
    queue.subscribe(() => changes++);
    const files = Array.from({ length: 50 }, (_, i) => file(`f${i}`, 0));
    const done = queue.add("p", files);
    // 1 s lang in stapjes van 10 ms
    for (let i = 0; i < 100; i++) {
      now += 10;
      await vi.advanceTimersByTimeAsync(10);
    }
    await done;
    expect(changes).toBeGreaterThan(50); // subscribe hoort alles
    expect(summaries).toBeLessThanOrEqual(5);
    expect(summaries).toBeGreaterThanOrEqual(1);
  });
});

describe("UploadQueue: pauzeren en hervatten", () => {
  it("pauzeren laat de upload op de server staan; hervatten gaat verder waar hij was", async () => {
    const server = new Server();
    const storage = memoryStorage();
    let calls = 0;
    let pausedAt = -1;
    const queue = new UploadQueue(server, {
      storage,
      chunkSize: 10,
      send: async (url, offset, chunk, token, onProgress, signal) => {
        calls++;
        if (calls === 2) {
          queue.pause(queue.getSnapshot()[0].key); // tijdens het tweede stuk
          pausedAt = offset;
        }
        if (signal?.aborted) throw new ApiError(0, "Geannuleerd", "aborted");
        return server.send(url, offset, chunk, token, onProgress, signal);
      },
    });
    const finished: boolean[] = [];
    queue.onFinished((_p, ok) => finished.push(ok));
    const done = queue.add("p", [file("film.mkv", 30)]);
    await tick();
    await tick();
    expect(queue.getSnapshot()).toMatchObject([{ state: "paused" }]);
    expect(pausedAt).toBe(10);
    expect(server.received.get("/u/1")).toBe(10); // staat nog op de server
    expect(storage.data.size).toBe(1);
    expect(finished).toEqual([]); // pauzeren is niet klaar
    expect(queue.getSummary()).toMatchObject({ paused: 1, running: 0 });

    let settled = false;
    void done.then(() => (settled = true));
    await tick();
    expect(settled).toBe(false); // add wacht op hervatten

    queue.resume(queue.getSnapshot()[0].key);
    await done;
    expect(server.created).toBe(1);
    expect(server.done.get("/u/1")).toBe("node-/u/1");
    expect(finished).toEqual([true]);
  });

  it("pauseAll en resumeAll, ook voor wat nog wacht", async () => {
    const server = new Server();
    const gate = doors();
    const send: SendChunk = async (url, ...rest) => {
      await gate.wait(url, rest[4]);
      if (rest[4]?.aborted) throw new ApiError(0, "Geannuleerd", "aborted");
      return server.send(url, ...rest);
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage(), concurrency: 2 });
    const done = queue.add("p", [file("a", 5), file("b", 5), file("c", 5)]);
    await tick();
    queue.pauseAll();
    expect(queue.getSnapshot().map((i) => i.state)).toEqual(["paused", "paused", "paused"]);
    await tick();
    expect(server.created).toBe(2); // c is nooit begonnen
    queue.resumeAll();
    await tick();
    for (let i = 1; i <= 4; i++) gate.open(`/u/${i}`);
    await done;
    expect(server.created).toBe(3); // a en b gingen verder met hun eigen upload
    expect(server.done.size).toBe(3);
  });

  it("pauzeren en meteen hervatten terwijl het aanmaken nog loopt: de upload gaat gewoon door", async () => {
    // tusCreate (en tusStatus) kennen het abort-signaal niet: de poging loopt dan nog af als
    // hervatten al geweest is. Dat is geen annuleren.
    const server = new Server();
    let release = () => {};
    const slow = new Promise<void>((resolve) => (release = resolve));
    const create = server.tusCreate.bind(server);
    server.tusCreate = async (...args) => {
      await slow;
      return create(...args);
    };
    const queue = new UploadQueue(server, { send: server.send, storage: memoryStorage() });
    const finished: boolean[] = [];
    queue.onFinished((_p, ok) => finished.push(ok));
    const done = queue.add("p", [file("a", 5)]);
    await tick();
    const [item] = queue.getSnapshot();
    queue.pause(item.key);
    queue.resume(item.key);
    release();
    await done;
    expect(queue.getSnapshot()).toEqual([]);
    expect(server.done.size).toBe(1);
    expect([...server.done.keys()]).toEqual(["/u/1"]); // dezelfde upload, verder waar hij was
    expect(finished).toEqual([true]);
  });

  it("annuleren na pauzeren en hervatten terwijl het aanmaken nog loopt, annuleert wel", async () => {
    const server = new Server();
    let release = () => {};
    const slow = new Promise<void>((resolve) => (release = resolve));
    const create = server.tusCreate.bind(server);
    server.tusCreate = async (...args) => {
      await slow;
      return create(...args);
    };
    const storage = memoryStorage();
    const queue = new UploadQueue(server, { send: server.send, storage });
    const done = queue.add("p", [file("a", 5)]);
    await tick();
    const [item] = queue.getSnapshot();
    queue.pause(item.key);
    queue.resume(item.key);
    queue.cancel(item.key);
    release();
    await done;
    await tick();
    expect(queue.getSnapshot()).toEqual([]);
    expect(server.done.size).toBe(0);
    // De upload die het aanmaken nog opleverde, is op de server weggegooid (geen gereserveerde
    // ruimte voor 24 uur) en staat niet meer in de opslag
    expect(server.cancelled).toEqual(["/u/1"]);
    expect(storage.data.size).toBe(0);
  });

  it("annuleren na pauzeren terwijl het aanmaken nog loopt: ook die upload wordt weggegooid", async () => {
    const server = new Server();
    let release = () => {};
    const slow = new Promise<void>((resolve) => (release = resolve));
    const create = server.tusCreate.bind(server);
    server.tusCreate = async (...args) => {
      await slow;
      return create(...args);
    };
    const storage = memoryStorage();
    const queue = new UploadQueue(server, { send: server.send, storage });
    const done = queue.add("p", [file("a", 5)]);
    await tick();
    const [item] = queue.getSnapshot();
    queue.pause(item.key);
    queue.cancel(item.key);
    await tick();
    release();
    await done;
    for (let i = 0; i < 5; i++) await tick();
    expect(queue.getSnapshot()).toEqual([]);
    expect(server.done.size).toBe(0);
    expect(server.cancelled).toEqual(["/u/1"]);
    expect(storage.data.size).toBe(0);
  });

  it("alleen pauzeren terwijl het aanmaken nog loopt: de upload blijft staan om verder te gaan", async () => {
    const server = new Server();
    let release = () => {};
    const slow = new Promise<void>((resolve) => (release = resolve));
    const create = server.tusCreate.bind(server);
    server.tusCreate = async (...args) => {
      await slow;
      return create(...args);
    };
    const storage = memoryStorage();
    const queue = new UploadQueue(server, { send: server.send, storage });
    const done = queue.add("p", [file("a", 5)]);
    await tick();
    const [item] = queue.getSnapshot();
    queue.pause(item.key);
    release();
    for (let i = 0; i < 5; i++) await tick();
    expect(server.cancelled).toEqual([]);
    expect(queue.getSnapshot()[0]).toMatchObject({ state: "paused", url: "/u/1" });
    queue.resume(item.key);
    await done;
    expect([...server.done.keys()]).toEqual(["/u/1"]);
  });

  it("annuleren van een gepauzeerde upload gooit hem op de server weg", async () => {
    const server = new Server();
    const gate = doors();
    const send: SendChunk = async (url, ...rest) => {
      await gate.wait(url, rest[4]);
      if (rest[4]?.aborted) throw new ApiError(0, "Geannuleerd", "aborted");
      return server.send(url, ...rest);
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage() });
    const done = queue.add("p", [file("a", 5)]);
    await tick();
    const [item] = queue.getSnapshot();
    queue.pause(item.key);
    await tick();
    queue.cancel(item.key);
    await done;
    expect(queue.getSnapshot()).toEqual([]);
    expect(server.cancelled).toEqual(["/u/1"]);
  });

  it("een wachtend bestand annuleren dat nog niet begon, vraagt de server niets", async () => {
    const server = new Server();
    const gate = doors();
    const send: SendChunk = async (url, ...rest) => {
      await gate.wait(url, rest[4]);
      return server.send(url, ...rest);
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage() });
    const done = queue.add("p", [file("a", 5), file("b", 5)]);
    await tick();
    queue.cancel(queue.getSnapshot()[1].key);
    expect(queue.getSnapshot()).toHaveLength(1);
    gate.open("/u/1");
    await done;
    expect(server.created).toBe(1);
    expect(server.cancelled).toEqual([]);
  });

  it("onCreate krijgt het id van elk nieuw bestand; mislukt geeft de reden met code", async () => {
    const server = new Server();
    const queue = new UploadQueue(server, {
      storage: memoryStorage(),
      send: async (url, ...rest) => {
        if (url === "/u/2") throw new ApiError(413, "Onvoldoende quotum", "no_room");
        return server.send(url, ...rest);
      },
    });
    const ids: string[] = [];
    await queue.add("p", [file("a", 1), file("b", 1)], { onCreate: (id) => ids.push(id) });
    expect(ids).toEqual(["node-/u/1"]);
    expect(queue.getSnapshot()).toMatchObject([
      { state: "failed", error: "Onvoldoende quotum", failure: { code: "no_room", message: "Onvoldoende quotum" } },
    ]);
    expect(queue.getSummary()).toMatchObject({ failed: 1, done: 1, files: 2 });
  });

  it("geeft conflict en lastmodified mee bij het aanmaken", async () => {
    const server = new Server();
    const queue = new UploadQueue(server, { send: server.send, storage: memoryStorage() });
    await queue.add("p", [file("a", 1, 1700000000000)]);
    await queue.add("p", [file("b", 1, 5)], { conflict: "replace" });
    expect(server.metas.map((m) => m.meta)).toEqual([
      { conflict: "rename", lastmodified: "1700000000000" },
      { conflict: "replace", lastmodified: "5" },
    ]);
  });
});

/** Nep-verzoeken voor addTree, met een logboek van wat er in welke volgorde gebeurde. */
function fakeRequests(log: string[], tree: (paths: string[]) => TreeResult, taken: Record<string, string[]>) {
  const requests: UploadRequests = {
    foldersTree: async (folderId, paths) => {
      log.push(`tree ${folderId} ${paths.length}`);
      return tree(paths);
    },
    namesCheck: async (folderId, names) => {
      log.push(`check ${folderId} ${names.length}`);
      const there = new Set((taken[folderId] ?? []).map((n) => n.toLowerCase()));
      return names.filter((n) => there.has(n.toLowerCase())).map((name) => ({ name, id: `id-${name}`, kind: "file" }));
    },
    pending: async () => [],
    discard: async () => {},
  };
  return requests;
}

class LoggingServer extends Server {
  constructor(private readonly log: string[]) {
    super();
  }
  override async tusCreate(parent: string, name: string, size: number, meta?: Record<string, string>) {
    this.log.push(`tus ${parent}/${name} ${meta?.conflict}`);
    return super.tusCreate(parent, name, size, meta);
  }
}

describe("UploadQueue.addTree", () => {
  it("mappen, dan namen alleen in bestaande mappen, dan één vraag, en pas daarna tus", async () => {
    const log: string[] = [];
    const server = new LoggingServer(log);
    let answer: (a: ConflictAnswer) => void = () => {};
    let asked: ConflictItem[] = [];
    const requests = fakeRequests(
      log,
      () => ({
        folders: { Vakantie: "vak", "Vakantie/Nieuw": "nieuw" },
        created: ["Vakantie/Nieuw"],
        errors: {},
      }),
      { root: ["los.txt"], vak: ["A.JPG", "b.jpg"] },
    );
    const queue = new UploadQueue(server, {
      send: server.send,
      storage: memoryStorage(),
      requests,
      ask: (items) => {
        asked = items;
        log.push(`ask ${items.length}`);
        return new Promise((resolve) => (answer = resolve));
      },
    });
    const done = queue.addTree("root", {
      dirs: ["Vakantie", "Vakantie/Nieuw"],
      files: [
        { file: file("los.txt", 1), path: "los.txt" },
        { file: file("a.jpg", 1), path: "Vakantie/a.jpg" },
        { file: file("b.jpg", 1), path: "Vakantie/b.jpg" },
        { file: file("c.jpg", 1), path: "Vakantie/c.jpg" },
        { file: file("d.jpg", 1), path: "Vakantie/Nieuw/d.jpg" },
      ],
    });
    await tick();
    await tick();
    expect(queue.busy()).toBe(true); // de lade staat er al tijdens de vraag
    expect(queue.getSummary().preparing).toBe(1);
    expect(asked.map((c) => c.path)).toEqual(["los.txt", "Vakantie/a.jpg", "Vakantie/b.jpg"]);
    expect(log.some((l) => l.startsWith("tus"))).toBe(false); // nog niets aangemaakt
    answer(["replace", "keep", "skip"]);
    await done;
    expect(log.slice(0, 4)).toEqual(["tree root 2", "check root 1", "check vak 3", "ask 3"]);
    expect(log.slice(4).sort()).toEqual([
      "tus nieuw/d.jpg rename",
      "tus root/los.txt replace",
      "tus vak/a.jpg rename",
      "tus vak/c.jpg rename",
    ]);
    expect(queue.busy()).toBe(false);
  });

  it("geen antwoord (dialoog dicht): niets uploaden", async () => {
    const log: string[] = [];
    const server = new LoggingServer(log);
    const queue = new UploadQueue(server, {
      send: server.send,
      storage: memoryStorage(),
      requests: fakeRequests(log, () => ({ folders: {}, created: [], errors: {} }), { root: ["a.txt"] }),
      ask: async () => null,
    });
    await queue.addTree("root", { dirs: [], files: [{ file: file("a.txt", 1), path: "a.txt" }, { file: file("b.txt", 1), path: "b.txt" }] });
    expect(log).toEqual(["check root 2"]);
    expect(queue.busy()).toBe(false);
  });

  it("een map die niet kan: zijn bestanden staan mislukt in de lade, de rest gaat door", async () => {
    const log: string[] = [];
    const server = new LoggingServer(log);
    const queue = new UploadQueue(server, {
      send: server.send,
      storage: memoryStorage(),
      requests: fakeRequests(
        log,
        () => ({ folders: { Ok: "ok" }, created: ["Ok"], errors: { Foto: "name_taken", "Foto/2024": "name_taken" } }),
        {},
      ),
      ask: async () => [],
    });
    await queue.addTree("root", {
      dirs: ["Foto", "Foto/2024", "Ok"],
      files: [
        { file: file("x.jpg", 1), path: "Foto/2024/x.jpg" },
        { file: file("y.jpg", 1), path: "Ok/y.jpg" },
      ],
    });
    expect(log).toEqual(["tree root 3", "tus ok/y.jpg rename"]); // in een nieuwe map geen namencontrole
    const [failed] = queue.getSnapshot();
    expect(failed).toMatchObject({
      state: "failed",
      retry: false,
      path: "Foto/2024/x.jpg",
      failure: { code: "name_taken", folder: "Foto" },
      error: "Er staat al een bestand met de naam van map Foto",
    });
    // Opnieuw kan niet; weggooien haalt hem weg zonder de server
    queue.retry(failed.key);
    await queue.run(failed.key);
    expect(queue.getSnapshot()[0].state).toBe("failed");
    await queue.discard(failed.key);
    expect(queue.getSnapshot()).toEqual([]);
    expect(server.cancelled).toEqual([]);
  });

  it("mappen per 1.000 paden en namen per 1.000", async () => {
    const log: string[] = [];
    const server = new LoggingServer(log);
    const dirs = Array.from({ length: 2500 }, (_, i) => `d${i}`);
    const queue = new UploadQueue(server, {
      send: server.send,
      storage: memoryStorage(),
      requests: fakeRequests(
        log,
        (paths) => ({ folders: Object.fromEntries(paths.map((p) => [p, `id-${p}`])), created: paths, errors: {} }),
        {},
      ),
      ask: async () => [],
    });
    const files = Array.from({ length: 1500 }, (_, i) => ({ file: file(`f${i}`, 0), path: `f${i}` }));
    await queue.addTree("root", { dirs, files });
    expect(log.filter((l) => !l.startsWith("tus"))).toEqual([
      "tree root 1000",
      "tree root 1000",
      "tree root 500",
      "check root 1000",
      "check root 500",
    ]);
  });

  it("leest grootte en datum per bestand één keer, en geeft tussendoor de hoofdthread terug", async () => {
    // In Chromium is elke lezing van size/lastModified van een gekozen bestand een synchrone
    // vraag aan de browser: hier kost elke lezing 0,1 ms en telt hij mee
    let reads = 0;
    const slow = (name: string) => {
      const f = file(name, 1);
      const size = f.size;
      const lastModified = f.lastModified;
      const busy = () => {
        reads++;
        const end = performance.now() + 0.1;
        while (performance.now() < end) {
          /* een synchrone vraag */
        }
      };
      Object.defineProperty(f, "size", { get: () => (busy(), size) });
      Object.defineProperty(f, "lastModified", { get: () => (busy(), lastModified) });
      return f;
    };
    const server = new Server();
    // De uploads blijven hangen bij het aanmaken: dan staan ze er nog als we kijken
    server.tusCreate = () => new Promise(() => {});
    const queue = new UploadQueue(server, {
      send: server.send,
      storage: memoryStorage(),
      requests: fakeRequests([], (paths) => ({ folders: {}, created: paths, errors: {} }), {}),
      ask: async () => [],
    });
    const files = Array.from({ length: 1000 }, (_, i) => ({ file: slow(`f${i}`), path: `f${i}` }));
    // Hoe lang de langste stap zonder onderbreking duurde (een taak in de browser)
    let longest = 0;
    let last = performance.now();
    let watching = true;
    const watch = () => {
      const now = performance.now();
      longest = Math.max(longest, now - last);
      last = now;
      if (watching) setTimeout(watch, 0);
    };
    setTimeout(watch, 0);
    const done = queue.addTree("root", { dirs: [], files });
    await vi.waitFor(() => expect(queue.getSummary().files).toBe(1000));
    watching = false;
    const before = reads;
    // size en lastModified, elk één keer (2.000), plus wat de ene upload die al begon leest
    expect(before).toBeGreaterThanOrEqual(2000);
    expect(before).toBeLessThan(2010);
    expect(longest).toBeLessThan(100); // 2.000 × 0,1 ms = 200 ms, maar in stukjes van ~30 ms
    expect(queue.getSummary().bytes).toBe(1000);
    queue.stopAll();
    await done;
  });
});
