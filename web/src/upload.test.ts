import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { type SendChunk, type TusApi, uploadFile } from "./upload";

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
