import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { discardUpload, type SendChunk, STOP, type TusApi, uploadFile, xhrSend } from "./upload";
import { UploadQueue } from "./uploadQueue";

/** Nep-server: houdt bij hoeveel bytes er binnen zijn, zoals de echte tus-server. */
class FakeServer implements TusApi {
  accessToken: string | null = "A1";
  received = new Map<string, number>();
  done = new Map<string, string>();
  sizes = new Map<string, number>();
  created = 0;
  refreshes = 0;

  async refresh() {
    this.refreshes++;
    this.accessToken = "A2";
    return {};
  }
  async tusCreate(_parent: string, _name: string, size: number) {
    const url = `/api/v1/tus/${++this.created}`;
    this.received.set(url, 0);
    this.sizes.set(url, size);
    if (size === 0) {
      this.done.set(url, "leeg");
      return { url, nodeId: "leeg" };
    }
    return { url, nodeId: null };
  }
  async tusStatus(url: string) {
    if (!this.received.has(url)) throw new ApiError(404, "weg");
    return { offset: this.received.get(url)!, nodeId: this.done.get(url) ?? null };
  }
  async tusCancel(url: string) {
    this.received.delete(url);
  }
  /** Normale PATCH: neemt alles aan en maakt het bestand als het compleet is. */
  send: SendChunk = async (url, offset, chunk, token, onProgress) => {
    if (token !== this.accessToken) throw new ApiError(401, "verlopen");
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

const file = (size: number) => new File([new Uint8Array(size)], "film.mkv", { lastModified: 1 });
const noSleep = async () => {};

describe("uploadFile", () => {
  it("stuurt het bestand in stukken en geeft het bestand terug", async () => {
    const server = new FakeServer();
    const progress: number[] = [];
    const storage = memoryStorage();
    const node = await uploadFile(server, "p", file(25), (f) => progress.push(f), {
      send: server.send,
      storage,
      chunkSize: 10,
    });
    expect(node).toBe("node-/api/v1/tus/1");
    expect(progress.at(-1)).toBe(1);
    expect(storage.data.size).toBe(0);
  });

  it("gaat na een netwerkfout verder waar de server was", async () => {
    const server = new FakeServer();
    let calls = 0;
    const flaky: SendChunk = async (url, offset, chunk, token, onProgress) => {
      calls++;
      if (calls === 2) {
        // De helft komt aan, dan valt de verbinding weg
        server.received.set(url, offset + 5);
        throw new ApiError(0, "Netwerkfout");
      }
      return server.send(url, offset, chunk, token, onProgress);
    };
    const node = await uploadFile(server, "p", file(30), () => {}, {
      send: flaky,
      storage: memoryStorage(),
      sleep: noSleep,
      chunkSize: 10,
    });
    expect(node).toBe("node-/api/v1/tus/1");
    expect(server.received.get("/api/v1/tus/1")).toBe(30);
    expect(server.created).toBe(1);
  });

  it("hervat na herladen van de pagina met dezelfde upload", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    let calls = 0;
    const dies: SendChunk = async (...args) => {
      if (++calls > 1) throw new ApiError(0, "Netwerkfout");
      return server.send(...args);
    };
    await expect(
      uploadFile(server, "p", file(30), () => {}, { send: dies, storage, sleep: noSleep, chunkSize: 10 }),
    ).rejects.toThrow("gaat verder waar hij was");
    expect(storage.data.size).toBe(1);

    const progress: number[] = [];
    const node = await uploadFile(server, "p", file(30), (f) => progress.push(f), {
      send: server.send,
      storage,
      chunkSize: 10,
    });
    expect(node).toBe("node-/api/v1/tus/1");
    expect(server.created).toBe(1);
    expect(progress[0]).toBeCloseTo(10 / 30);
  });

  it("vraagt het bestand op als het laatste antwoord verloren ging", async () => {
    const server = new FakeServer();
    let lost = false;
    const lossy: SendChunk = async (...args) => {
      const result = await server.send(...args);
      if (result.nodeId && !lost) {
        lost = true;
        throw new ApiError(0, "Netwerkfout");
      }
      return result;
    };
    const node = await uploadFile(server, "p", file(10), () => {}, {
      send: lossy,
      storage: memoryStorage(),
      sleep: noSleep,
    });
    expect(node).toBe("node-/api/v1/tus/1");
  });

  it("ververst het token als het halverwege verloopt", async () => {
    const server = new FakeServer();
    const expiring: SendChunk = async (url, offset, chunk, token, onProgress) => {
      if (offset === 10 && server.refreshes === 0) server.accessToken = "verlopen";
      return server.send(url, offset, chunk, token, onProgress);
    };
    const node = await uploadFile(server, "p", file(20), () => {}, {
      send: expiring,
      storage: memoryStorage(),
      chunkSize: 10,
    });
    expect(node).toBe("node-/api/v1/tus/1");
    expect(server.refreshes).toBe(1);
  });

  it("begint opnieuw als de oude upload verlopen is", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    storage.setItem("platform-upload:p:film.mkv:10:1", "/api/v1/tus/oud");
    const node = await uploadFile(server, "p", file(10), () => {}, { send: server.send, storage });
    expect(node).toBe("node-/api/v1/tus/1");
  });

  it("leeg bestand is meteen klaar", async () => {
    const server = new FakeServer();
    expect(await uploadFile(server, "p", file(0), () => {}, { send: server.send, storage: memoryStorage() })).toBe(
      "leeg",
    );
  });

  it("geeft op bij een fout die opnieuw proberen niet oplost", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    const full: SendChunk = async () => {
      throw new ApiError(413, "Onvoldoende quotum");
    };
    await expect(uploadFile(server, "p", file(10), () => {}, { send: full, storage })).rejects.toThrow(
      "Onvoldoende quotum",
    );
    expect(storage.data.size).toBe(0);
  });

  it("annuleren ruimt de upload op", async () => {
    const server = new FakeServer();
    const controller = new AbortController();
    const slow: SendChunk = async () => {
      controller.abort();
      throw new ApiError(0, "Geannuleerd", "aborted");
    };
    await expect(
      uploadFile(server, "p", file(10), () => {}, { send: slow, storage: memoryStorage(), signal: controller.signal }),
    ).rejects.toMatchObject({ code: "aborted" });
    expect(server.received.size).toBe(0);
  });
});

const BUSY = "Er loopt al een verzoek voor deze upload";

describe("uploadFile bij 409", () => {
  it("kijkt na de eerste 409 meteen opnieuw, en wacht daarna steeds langer", async () => {
    const server = new FakeServer();
    const sleeps: number[] = [];
    let conflicts = 3;
    const send: SendChunk = async (...args) => {
      if (conflicts-- > 0) throw new ApiError(409, "offset");
      return server.send(...args);
    };
    const node = await uploadFile(server, "p", file(10), () => {}, {
      send,
      storage: memoryStorage(),
      sleep: async (ms) => void sleeps.push(ms),
    });
    expect(node).toBe("node-/api/v1/tus/1");
    expect(sleeps).toEqual([2000, 4000]);
  });

  it("wacht altijd als er al een ander verzoek voor de upload loopt", async () => {
    const server = new FakeServer();
    const sleeps: number[] = [];
    let busy = 2;
    const send: SendChunk = async (...args) => {
      if (busy-- > 0) throw new ApiError(409, BUSY, "upload_busy");
      return server.send(...args);
    };
    const node = await uploadFile(server, "p", file(10), () => {}, {
      send,
      storage: memoryStorage(),
      sleep: async (ms) => void sleeps.push(ms),
    });
    expect(node).toBe("node-/api/v1/tus/1");
    expect(sleeps).toEqual([1000, 2000]);
  });

  it("blijft het bezet, dan de melding van de server, en de upload blijft staan", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    const send: SendChunk = async () => {
      throw new ApiError(409, BUSY, "upload_busy");
    };
    await expect(uploadFile(server, "p", file(10), () => {}, { send, storage, sleep: noSleep })).rejects.toMatchObject({
      message: BUSY,
      code: "upload_busy",
    });
    expect(storage.data.size).toBe(1);
    expect(server.received.size).toBe(1);
  });

  it("stopt meteen als de naam intussen bezet is", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    let sends = 0;
    const send: SendChunk = async () => {
      sends++;
      throw new ApiError(409, "Er bestaat al een bestand met deze naam", "name_taken");
    };
    await expect(uploadFile(server, "p", file(10), () => {}, { send, storage, sleep: noSleep })).rejects.toMatchObject({
      message: "Er bestaat al een bestand met deze naam",
      code: "name_taken",
    });
    expect(sends).toBe(1);
    expect(storage.data.size).toBe(0);
  });
});

describe("uploadFile annuleren en stoppen", () => {
  it("annuleren werkt ook tijdens het wachten na een fout", async () => {
    const server = new FakeServer();
    const controller = new AbortController();
    const send: SendChunk = async () => {
      throw new ApiError(0, "Netwerkfout");
    };
    // Wacht 'voor altijd'; alleen annuleren kan het korter maken
    const sleep = () => {
      controller.abort();
      return new Promise<void>(() => {});
    };
    await expect(
      uploadFile(server, "p", file(10), () => {}, { send, sleep, storage: memoryStorage(), signal: controller.signal }),
    ).rejects.toMatchObject({ code: "aborted" });
    expect(server.received.size).toBe(0);
  }, 2000);

  it("stuurt niets meer als er tijdens het opvragen van de stand geannuleerd wordt", async () => {
    const server = new FakeServer();
    const controller = new AbortController();
    let sends = 0;
    const send: SendChunk = async (...args) => {
      if (++sends === 1) throw new ApiError(0, "Netwerkfout");
      return server.send(...args);
    };
    const status = server.tusStatus.bind(server);
    server.tusStatus = async (url: string) => {
      const result = await status(url);
      controller.abort();
      return result;
    };
    await expect(
      uploadFile(server, "p", file(10), () => {}, {
        send,
        sleep: noSleep,
        storage: memoryStorage(),
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "aborted" });
    expect(sends).toBe(1);
    expect(server.done.size).toBe(0);
  });

  it("was het bestand al af bij annuleren, dan blijft het bestaan", async () => {
    const server = new FakeServer();
    const controller = new AbortController();
    const send: SendChunk = async (...args) => {
      const result = await server.send(...args);
      controller.abort();
      return result;
    };
    const node = await uploadFile(server, "p", file(10), () => {}, {
      send,
      storage: memoryStorage(),
      signal: controller.signal,
    });
    expect(node).toBe("node-/api/v1/tus/1");
    expect(server.received.size).toBe(1);
  });

  it("stoppen laat de upload op de server staan om later verder te gaan", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    const controller = new AbortController();
    const send: SendChunk = async () => {
      controller.abort(STOP);
      throw new ApiError(0, "Geannuleerd", "aborted");
    };
    await expect(
      uploadFile(server, "p", file(10), () => {}, { send, storage, signal: controller.signal }),
    ).rejects.toMatchObject({ code: "aborted" });
    expect(server.received.size).toBe(1);
    expect(storage.data.size).toBe(1);
  });
});

describe("discardUpload", () => {
  it("gooit de upload op de server weg en vergeet hem", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    const { url } = await server.tusCreate("p", "film.mkv", 10);
    storage.setItem("platform-upload:p:film.mkv:10:1", url);
    await discardUpload(server, "p", file(10), undefined, storage);
    expect(server.received.size).toBe(0);
    expect(storage.data.size).toBe(0);
  });

  it("werkt zonder localStorage met de URL die de upload meldde", async () => {
    const server = new FakeServer();
    const { url } = await server.tusCreate("p", "film.mkv", 10);
    await discardUpload(server, "p", file(10), url, brokenStorage());
    expect(server.received.size).toBe(0);
  });

  it("houdt alles vast als weggooien mislukt, maar een verlopen upload is goed", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    storage.setItem("platform-upload:p:film.mkv:10:1", "/api/v1/tus/x");
    server.tusCancel = async () => {
      throw new ApiError(0, "Netwerkfout");
    };
    await expect(discardUpload(server, "p", file(10), undefined, storage)).rejects.toThrow("Netwerkfout");
    expect(storage.data.size).toBe(1);

    server.tusCancel = async () => {
      throw new ApiError(404, "weg");
    };
    await discardUpload(server, "p", file(10), undefined, storage);
    expect(storage.data.size).toBe(0);
  });
});

/** localStorage die niet werkt, zoals in een privévenster met opslag uit. */
function brokenStorage() {
  const fail = () => {
    throw new Error("geen opslag");
  };
  return { getItem: fail, setItem: fail, removeItem: fail };
}

/** Nep-XMLHttpRequest: de test bepaalt het antwoord. */
class FakeXhr {
  static last: FakeXhr;
  static sent = 0;
  aborted = 0;
  status = 0;
  responseText = "";
  headers: Record<string, string> = {};
  upload: { onprogress: ((e: { loaded: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() {
    FakeXhr.last = this;
  }
  open() {}
  setRequestHeader() {}
  getResponseHeader(name: string) {
    return this.headers[name] ?? null;
  }
  send() {
    FakeXhr.sent++;
  }
  abort() {
    this.aborted++;
    this.onabort?.();
  }
  respond(status: number, body: string, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = body;
    this.headers = headers;
    this.onload?.();
  }
}

describe("xhrSend", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stuurt niets als er al geannuleerd is", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const controller = new AbortController();
    controller.abort();
    const before = FakeXhr.sent;
    await expect(xhrSend("/u", 0, new Blob(["x"]), "A1", () => {}, controller.signal)).rejects.toMatchObject({
      code: "aborted",
    });
    expect(FakeXhr.sent).toBe(before);
  }, 2000);

  it("geeft de code van de server door en laat het signaal na afloop los", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const controller = new AbortController();
    const busy = xhrSend("/u", 0, new Blob(["x"]), "A1", () => {}, controller.signal);
    FakeXhr.last.respond(409, JSON.stringify({ detail: { message: BUSY, code: "upload_busy" } }));
    await expect(busy).rejects.toMatchObject({ status: 409, message: BUSY, code: "upload_busy" });

    const ok = xhrSend("/u", 0, new Blob(["x"]), "A1", () => {}, controller.signal);
    const xhr = FakeXhr.last;
    xhr.respond(204, "", { "Upload-Offset": "1", "Platform-Node-Id": "n1" });
    await expect(ok).resolves.toEqual({ offset: 1, nodeId: "n1" });
    controller.abort(); // de volgende upload gebruikt dit signaal nog; dit verzoek is al klaar
    expect(xhr.aborted).toBe(0);
  });
});

/** Een deur die dicht blijft tot de test hem opent. */
function gate() {
  let open = () => {};
  const closed = new Promise<void>((resolve) => (open = resolve));
  return { closed, open: () => open() };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("UploadQueue", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("start hetzelfde bestand in dezelfde map niet nog eens terwijl het loopt", async () => {
    const server = new FakeServer();
    const door = gate();
    let sends = 0;
    const send: SendChunk = async (...args) => {
      sends++;
      await door.closed;
      return server.send(...args);
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage(), sleep: noSleep });
    const first = queue.add("p", [file(10)]);
    await tick();
    expect(queue.getSnapshot()[0].state).toBe("running");
    const again = queue.add("p", [file(10)]); // zelfde naam, grootte en datum
    expect(queue.getSnapshot()).toHaveLength(1);
    door.open();
    await Promise.all([first, again]);
    expect(server.created).toBe(1);
    expect(sends).toBe(1);
    expect(queue.getSnapshot()).toHaveLength(0);
  });

  it("een mislukte upload opnieuw kiezen gaat verder met dezelfde upload", async () => {
    const server = new FakeServer();
    let broken = true;
    const send: SendChunk = async (...args) => {
      if (broken) throw new ApiError(0, "Netwerkfout");
      return server.send(...args);
    };
    const finished: string[] = [];
    const queue = new UploadQueue(server, { send, storage: memoryStorage(), sleep: noSleep });
    queue.onFinished((parent) => finished.push(parent));
    await queue.add("p", [file(10)]);
    expect(queue.getSnapshot()).toMatchObject([{ state: "failed", url: "/api/v1/tus/1" }]);
    broken = false;
    await queue.add("p", [file(10)]);
    expect(queue.getSnapshot()).toHaveLength(0);
    expect(server.created).toBe(1);
    expect(server.done.get("/api/v1/tus/1")).toBe("node-/api/v1/tus/1");
    expect(finished).toEqual(["p", "p"]);
  });

  it("weggooien werkt ook zonder localStorage, en de server geeft de ruimte vrij", async () => {
    const server = new FakeServer();
    const send: SendChunk = async () => {
      throw new ApiError(0, "Netwerkfout");
    };
    const queue = new UploadQueue(server, { send, storage: brokenStorage(), sleep: noSleep });
    await queue.add("p", [file(10)]);
    const [item] = queue.getSnapshot();
    expect(item.state).toBe("failed");
    await queue.discard(item.key);
    expect(queue.getSnapshot()).toHaveLength(0);
    expect(server.received.size).toBe(0);
  });

  it("lukt weggooien niet, dan blijft de upload in de lijst met de fout", async () => {
    const server = new FakeServer();
    const send: SendChunk = async () => {
      throw new ApiError(0, "Netwerkfout");
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage(), sleep: noSleep });
    await queue.add("p", [file(10)]);
    server.tusCancel = async () => {
      throw new ApiError(0, "Netwerkfout");
    };
    await queue.discard(queue.getSnapshot()[0].key);
    expect(queue.getSnapshot()).toMatchObject([{ state: "failed", error: "Weggooien lukte niet: Netwerkfout" }]);
  });

  it("annuleren haalt de upload weg, ook op de server", async () => {
    const server = new FakeServer();
    const door = gate();
    const send: SendChunk = async (_url, _offset, _chunk, _token, _progress, signal) => {
      await door.closed;
      if (signal?.aborted) throw new ApiError(0, "Geannuleerd", "aborted");
      throw new ApiError(0, "Netwerkfout");
    };
    const queue = new UploadQueue(server, { send, storage: memoryStorage(), sleep: noSleep });
    const running = queue.add("p", [file(10)]);
    await tick();
    queue.cancel(queue.getSnapshot()[0].key);
    door.open();
    await running;
    expect(queue.getSnapshot()).toHaveLength(0);
    expect(server.received.size).toBe(0);
  });

  it("stopAll (uitloggen) maakt de lijst leeg maar laat de upload staan om later verder te gaan", async () => {
    const server = new FakeServer();
    const storage = memoryStorage();
    const door = gate();
    const send: SendChunk = async (_url, _offset, _chunk, _token, _progress, signal) => {
      await door.closed;
      if (signal?.aborted) throw new ApiError(0, "Geannuleerd", "aborted");
      throw new ApiError(0, "Netwerkfout");
    };
    const queue = new UploadQueue(server, { send, storage, sleep: noSleep });
    const running = queue.add("p", [file(10), file(20)]);
    await tick();
    queue.stopAll();
    expect(queue.getSnapshot()).toHaveLength(0);
    door.open();
    await running;
    expect(server.created).toBe(1); // het tweede bestand begint niet meer
    expect(server.received.size).toBe(1);
    expect(storage.data.size).toBe(1);
  });

  it("heeft geen crypto.randomUUID nodig (bestaat niet via http op het LAN-adres)", async () => {
    vi.stubGlobal("crypto", {});
    const server = new FakeServer();
    const queue = new UploadQueue(server, { send: server.send, storage: memoryStorage() });
    await queue.add("p", [file(10), new File([new Uint8Array(5)], "b.txt", { lastModified: 1 })]);
    expect(server.done.size).toBe(2);
  });
});
