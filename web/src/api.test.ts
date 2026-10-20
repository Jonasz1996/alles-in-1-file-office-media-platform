import { describe, expect, it } from "vitest";
import { fetchClusterStatus, fetchInfo, formatBytes, UnauthorizedError } from "./api";

const info = { name: "platform-api", version: "0.1.0", environment: "test", node: "n1" };

describe("fetchInfo", () => {
  it("geeft de API-info terug", async () => {
    const fakeFetch = (async () => new Response(JSON.stringify(info))) as typeof fetch;
    await expect(fetchInfo(fakeFetch)).resolves.toEqual(info);
  });

  it("gooit een fout bij een foutstatus", async () => {
    const fakeFetch = (async () => new Response("", { status: 503 })) as typeof fetch;
    await expect(fetchInfo(fakeFetch)).rejects.toThrow("503");
  });
});

describe("fetchClusterStatus", () => {
  it("stuurt het token mee", async () => {
    let auth = "";
    const fakeFetch = (async (_url: string, init?: RequestInit) => {
      auth = new Headers(init?.headers).get("Authorization") ?? "";
      return new Response(JSON.stringify({ status: "ok", nodes: [], database: [], messages: [] }));
    }) as typeof fetch;
    await expect(fetchClusterStatus("abc", fakeFetch)).resolves.toMatchObject({ status: "ok" });
    expect(auth).toBe("Bearer abc");
  });

  it("geeft een UnauthorizedError bij 401", async () => {
    const fakeFetch = (async () => new Response("", { status: 401 })) as typeof fetch;
    await expect(fetchClusterStatus("x", fakeFetch)).rejects.toBeInstanceOf(UnauthorizedError);
  });
});

describe("formatBytes", () => {
  it("rekent om naar leesbare eenheden", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1,5 KB");
    expect(formatBytes(700 * 1024 ** 3)).toBe("700,0 GB");
  });
});
