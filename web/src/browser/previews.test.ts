import { describe, expect, it } from "vitest";
import type { FileNode, Preview } from "../api";
import { PreviewCache } from "./previews";

const file = (id: string, etag = "e1") => ({ id, kind: "file", etag }) as unknown as FileNode;
const ready = { state: "ready", url: "/p/a.webp" } as unknown as Preview;

/** Een fetch die we zelf laten slagen of mislukken */
function controlled() {
  const calls: { ids: string[]; resolve(v: Record<string, Preview>): void; reject(e: unknown): void }[] = [];
  const fetch = (ids: string[]) =>
    new Promise<Record<string, Preview>>((resolve, reject) => {
      calls.push({ ids, resolve, reject });
    });
  return { calls, fetch };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe("pending: wacht het antwoord over de preview nog?", () => {
  it("ja tot het antwoord er is, daarna nee (ook zonder preview)", async () => {
    const f = controlled();
    const cache = new PreviewCache(f.fetch, () => 0);
    expect(cache.pending("a", "e1")).toBe(true);
    cache.want([file("a"), file("b")]);
    expect(f.calls).toHaveLength(1);
    expect(cache.pending("a", "e1")).toBe(true);
    f.calls[0].resolve({ a: ready });
    await settle();
    expect(cache.pending("a", "e1")).toBe(false);
    expect(cache.get("a", "e1")).toBe(ready);
    // b heeft geen preview: ook geen wachten meer (dan het icoon)
    expect(cache.pending("b", "e1")).toBe(false);
    // andere inhoud: weer wachten op een nieuw antwoord
    expect(cache.pending("a", "e2")).toBe(true);
  });

  it("na een mislukt verzoek niet meer (dan het icoon), en een volgende keer opnieuw", async () => {
    const f = controlled();
    let now = 0;
    const cache = new PreviewCache(f.fetch, () => now);
    let told = 0;
    cache.subscribe(() => told++);
    cache.want([file("a")]);
    f.calls[0].reject(new Error("offline"));
    await settle();
    expect(cache.pending("a", "e1")).toBe(false);
    expect(told).toBe(1);
    now = 1000;
    cache.want([file("a")]);
    expect(f.calls).toHaveLength(2);
    f.calls[1].resolve({ a: ready });
    await settle();
    expect(cache.get("a", "e1")).toBe(ready);
    expect(cache.pending("a", "e1")).toBe(false);
  });
});
