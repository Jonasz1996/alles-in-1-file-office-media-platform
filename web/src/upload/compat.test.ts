// De wachtrij zoals Public.tsx, public.test.ts en sharing.test.ts hem gebruiken (P3 is daar
// niet de eigenaar van): constructor (api, {send, storage, chunkSize}), await add → elke
// onFinished is geweest → getSnapshot() is []. Dat moet zo blijven (regel 2).

import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import type { SendChunk, TusApi } from "../upload";
import { UploadQueue } from "../uploadQueue";

/** Zoals PublicLink: tusCreate met drie argumenten (de vierde, meta, negeert hij). */
class ThreeArgApi implements TusApi {
  accessToken = "k";
  received = new Map<string, number>();
  sizes = new Map<string, number>();
  created = 0;
  async refresh() {
    return true;
  }
  async tusCreate(_parent: string, _name: string, size: number) {
    const url = `/public/${++this.created}`;
    this.received.set(url, 0);
    this.sizes.set(url, size);
    return { url, nodeId: size === 0 ? "n" : null };
  }
  async tusStatus(url: string) {
    return { offset: this.received.get(url) ?? 0, nodeId: null };
  }
  async tusCancel() {}
  send: SendChunk = async (url, offset, chunk, _t, onProgress) => {
    const now = offset + chunk.size;
    this.received.set(url, now);
    onProgress(chunk.size);
    // Via een link komt er nooit een id terug; wel klaar als alles binnen is
    return { offset: now, nodeId: now === this.sizes.get(url) ? "" : null };
  };
}

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

const file = (size: number, name: string) => new File([new Uint8Array(size)], name, { lastModified: 1 });

describe("compat: patronen van public.test.ts en sharing.test.ts", () => {
  it("await add → elke onFinished (in volgorde) → getSnapshot() is []", async () => {
    const link = new ThreeArgApi();
    const queue = new UploadQueue(link, { send: link.send, storage: memoryStorage(), chunkSize: 10 });
    const arrived: string[] = [];
    queue.onFinished((_parent, ok, f) => ok && arrived.push(f.name));
    await queue.add("public:root-1", [file(15, "a.jpg"), file(5, "b.jpg")]);
    expect(arrived).toEqual(["a.jpg", "b.jpg"]);
    expect(queue.getSnapshot()).toEqual([]);
  });

  it("ok en het bestand, ook bij mislukken; add is ook dan klaar", async () => {
    let fail = false;
    const api: TusApi = {
      accessToken: "k",
      refresh: async () => true,
      tusCreate: async () => {
        if (fail) throw new Error("Onvoldoende quotum");
        return { url: "/u/1", nodeId: "n" };
      },
      tusStatus: async () => ({ offset: 0, nodeId: null }),
      tusCancel: async () => {},
    };
    const queue = new UploadQueue(api, { storage: { getItem: () => null, setItem: () => {}, removeItem: () => {} } });
    const finished: [string, boolean, string][] = [];
    queue.onFinished((parent, ok, f) => finished.push([parent, ok, f.name]));
    await queue.add("p", [new File([], "a.txt")]);
    expect(queue.getSnapshot()).toEqual([]);
    fail = true;
    await queue.add("p", [new File([], "b.txt")]);
    expect(finished).toEqual([
      ["p", true, "a.txt"],
      ["p", false, "b.txt"],
    ]);
    expect(queue.getSnapshot()).toMatchObject([{ state: "failed", error: "Onvoldoende quotum" }]);
  });

  it("getSnapshot is stabiel tussen wijzigingen en nieuw na elke wijziging", async () => {
    const link = new ThreeArgApi();
    const queue = new UploadQueue(link, { send: link.send, storage: memoryStorage() });
    const empty = queue.getSnapshot();
    expect(empty).toEqual([]);
    expect(queue.getSnapshot()).toBe(empty);
    const seen: (readonly unknown[])[] = [];
    queue.subscribe(() => seen.push(queue.getSnapshot()));
    const done = queue.add("p", [file(3, "x")]);
    const first = queue.getSnapshot();
    expect(queue.getSnapshot()).toBe(first);
    await done;
    // Elke melding gaf een nieuwe array (niet samengevoegd)
    for (let i = 1; i < seen.length; i++) expect(seen[i]).not.toBe(seen[i - 1]);
    expect(seen.at(-1)).toEqual([]);
  });

  it("een fout zonder ApiError (TypeError van fetch) wordt een mislukte upload, geen crash", async () => {
    const api: TusApi = {
      accessToken: null,
      refresh: async () => null,
      tusCreate: async () => {
        throw new ApiError(0, "Geen verbinding met de server. Probeer het opnieuw.", "network");
      },
      tusStatus: async () => ({ offset: 0, nodeId: null }),
      tusCancel: async () => {},
    };
    const queue = new UploadQueue(api, { storage: memoryStorage() });
    await queue.add("p", [file(1, "a")]);
    expect(queue.getSnapshot()[0]).toMatchObject({ state: "failed", failure: { code: "network" } });
  });
});
