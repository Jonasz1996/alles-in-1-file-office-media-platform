import { describe, expect, it } from "vitest";
import { formatDuration, mayRenew, playerFor } from "./media";
import { readRange, UrlMemo } from "./source";

describe("formatDuration", () => {
  it("minuten en seconden, met uren als dat nodig is", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(83.4)).toBe("1:23");
    expect(formatDuration(3723)).toBe("1:02:03");
  });
});

describe("playerFor", () => {
  it("een speler voor video en audio, behalve als de inhoud tekst of een archief bleek", () => {
    expect(playerFor("video", {})).toBe("video");
    expect(playerFor("audio", { duration: 3 })).toBe("audio");
    expect(playerFor("video", { text: "#EXTM3U" })).toBeNull(); // playlist.mp4 die tekst is
    expect(playerFor("video", { entries: [] })).toBeNull();
    expect(playerFor("image", {})).toBeNull();
  });
});

describe("mayRenew", () => {
  it("hooguit drie nieuwe links kort na elkaar, na een stille minuut weer wel", () => {
    const state = { count: 0, at: 0 };
    let now = 1_000_000;
    expect([1, 2, 3, 4].map(() => mayRenew(state, (now += 1000)))).toEqual([true, true, true, false]);
    // Lang afspelen: de link verloopt elke tien minuten, dat blijft gewoon werken
    for (let i = 0; i < 5; i++) expect(mayRenew(state, (now += 10 * 60_000))).toBe(true);
  });
});

describe("UrlMemo", () => {
  it("hergebruikt een adres tot een minuut voor het verloopt", async () => {
    let now = 0;
    let made = 0;
    const memo = new UrlMemo(() => now);
    const make = async () => ({ url: `/d?token=${++made}`, expires_in: 600 });
    expect(await memo.get("a", make)).toBe("/d?token=1");
    now = 539_000;
    expect(await memo.get("a", make)).toBe("/d?token=1");
    now = 540_000;
    expect(await memo.get("a", make)).toBe("/d?token=2");
    expect(await memo.get("b", make)).toBe("/d?token=3"); // een ander bestand of een andere versie
    memo.forget("a");
    expect(await memo.get("a", make)).toBe("/d?token=4");
  });
});

describe("readRange", () => {
  it("206 met Content-Range, of de hele inhoud geknipt", async () => {
    const seen: string[] = [];
    const partial = async (_url: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get("Range") ?? "");
      return new Response(new Uint8Array([1, 2, 3]), { status: 206, headers: { "Content-Range": "bytes 0-2/10" } });
    };
    expect(await readRange("/x", 0, 3, partial as typeof fetch)).toEqual({ bytes: new Uint8Array([1, 2, 3]), total: 10 });
    expect(seen).toEqual(["bytes=0-2"]);
    const whole = async () => new Response(new Uint8Array([1, 2, 3, 4, 5]), { status: 200 });
    expect(await readRange("/x", 1, 3, whole as typeof fetch)).toEqual({ bytes: new Uint8Array([2, 3]), total: 5 });
    const beyond = async () => new Response(null, { status: 416 });
    expect((await readRange("/x", 10, 20, beyond as typeof fetch)).bytes).toHaveLength(0);
    const gone = async () => new Response(null, { status: 401 });
    await expect(readRange("/x", 0, 3, gone as typeof fetch)).rejects.toThrow();
  });
});
