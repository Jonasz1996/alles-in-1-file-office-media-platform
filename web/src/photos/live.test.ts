import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Preview } from "../api";
import type { ServerEvent } from "../events";
import type { PhotoItem, PhotoPage } from "./api";
import { fromPreview, reorders, ThumbPatcher } from "./live";
import { SectionStore } from "./store";

function item(id: string): PhotoItem {
  return {
    id,
    name: `${id}.jpg`,
    mime: "image/jpeg",
    size: 1,
    modified_at: "2026-11-01T10:00:00Z",
    media: "image",
    taken_at: "2026-11-01T10:00:00Z",
    taken_day: 20261101,
    width: null,
    height: null,
    duration: null,
    etag: `"${id}"`,
    thumb: null,
    large: null,
  };
}

const ready = (id: string): Preview => ({
  state: "ready",
  thumb: `/api/v1/previews/${id}/256.webp`,
  large: `/api/v1/previews/${id}/1024.webp`,
  meta: { width: 4000, height: 3000 },
});

const event = (data: Record<string, unknown>): ServerEvent => ({ type: "preview.ready", data }) as ServerEvent;

/** Twee maanden: a, b in de eerste, c in de tweede. */
async function loaded(): Promise<SectionStore> {
  const pages: Record<number, PhotoItem[]> = { 0: [item("a"), item("b")], 1: [item("c")] };
  const store = new SectionStore(async (s): Promise<PhotoPage> => ({ items: pages[s], next: null }), [2, 1]);
  await store.load(0);
  await store.load(1);
  return store;
}

describe("SectionStore.patch: een thumbnail erin zonder opnieuw te laden", () => {
  it("alleen de sectie waarin iets veranderde, krijgt een nieuwe array", async () => {
    const store = await loaded();
    const first = store.section(0);
    const second = store.section(1);
    const version = store.version;
    expect(store.patch(new Map([["b", { thumb: "/t/b" }]]))).toBe(1);
    expect(store.section(0)).not.toBe(first);
    expect(store.section(0)?.[1].thumb).toBe("/t/b");
    expect(store.section(0)?.[0]).toBe(first?.[0]);
    expect(store.section(1)).toBe(second);
    expect(store.version).toBe(version + 1);
    // De plek blijft
    expect(store.indexOf("b")).toBe(1);
    // Niets geladen met die id: niets veranderd, geen nieuwe versie
    expect(store.patch(new Map([["x", { thumb: "/t/x" }]]))).toBe(0);
    expect(store.version).toBe(version + 1);
  });
});

describe("ThumbPatcher: preview.ready zonder alles opnieuw te lezen", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("veel preview.ready vlak na elkaar: één POST /previews met alleen wat geladen is", async () => {
    const store = await loaded();
    const fetch = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, ready(id)])));
    const patcher = new ThumbPatcher(store, fetch, 500);
    patcher.onEvent(event({ sha: "1", node_ids: ["a"] }));
    patcher.onEvent(event({ sha: "2", node_ids: ["c", "niet-geladen"] }));
    patcher.onEvent({ type: "node.created", data: { id: "a" } } as ServerEvent);
    expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0].sort()).toEqual(["a", "c"]);
    expect(store.section(0)?.[0].thumb).toBe("/api/v1/previews/a/256.webp");
    expect(store.section(0)?.[0].width).toBe(4000);
    expect(store.section(0)?.[1].thumb).toBeNull();
    expect(store.section(1)?.[0].large).toBe("/api/v1/previews/c/1024.webp");
    // Daarna weer een bundel
    patcher.onEvent(event({ node_ids: ["b"] }));
    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(store.section(0)?.[1].thumb).toBe("/api/v1/previews/b/256.webp");
  });

  it("niets geladen van wat klaar is: geen verzoek", async () => {
    const store = await loaded();
    const fetch = vi.fn(async () => ({}));
    const patcher = new ThumbPatcher(store, fetch, 500);
    patcher.onEvent(event({ node_ids: ["x", "y"] }));
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("met een nieuwe opnamedatum (dated) laat de pagina zelf opnieuw laden", async () => {
    const store = await loaded();
    const fetch = vi.fn(async () => ({}));
    const patcher = new ThumbPatcher(store, fetch, 500);
    const dated = event({ node_ids: ["a"], dated: true });
    expect(reorders(dated)).toBe(true);
    expect(reorders(event({ node_ids: ["a"] }))).toBe(false);
    patcher.onEvent(dated);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("mislukt het verzoek, dan blijven de plaatshouders; na stop gebeurt er niets meer", async () => {
    const store = await loaded();
    const fetch = vi.fn(async (): Promise<Record<string, Preview>> => {
      throw new Error("offline");
    });
    const patcher = new ThumbPatcher(store, fetch, 500);
    patcher.onEvent(event({ node_ids: ["a"] }));
    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(store.section(0)?.[0].thumb).toBeNull();
    patcher.onEvent(event({ node_ids: ["a"] }));
    patcher.stop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("fromPreview neemt alleen getallen uit meta", () => {
    expect(fromPreview({ state: "ready", thumb: "/t", large: null, meta: { duration: 8 } })).toEqual({
      thumb: "/t",
      large: null,
      duration: 8,
    });
  });
});
