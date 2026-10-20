import { describe, expect, it } from "vitest";
import { fetchInfo } from "./api";

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
