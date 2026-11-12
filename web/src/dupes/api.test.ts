import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import * as dupes from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("de aanroepen van de duplicaten (§3.4, Z3)", () => {
  it("elke functie gebruikt de methode en het pad van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    await dupes.duplicates("exact");
    await dupes.duplicates("near", "c/2");
    await dupes.ignoreDuplicates("near:abc");

    expect(request.mock.calls).toEqual([
      ["GET", "/api/v1/duplicates?kind=exact"],
      ["GET", "/api/v1/duplicates?kind=near&cursor=c%2F2"],
      ["POST", "/api/v1/duplicates/ignore", { key: "near:abc" }],
    ]);
  });
});
