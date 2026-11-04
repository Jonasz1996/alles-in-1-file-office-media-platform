import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import * as admin from "./searchApi";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("de aanroepen van de tab Zoeken en foto's (§3.4, Z7; gezichten en beeld van Z0b)", () => {
  it("elke functie gebruikt de methode en het pad van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    await admin.adminSearch();
    await admin.patchAdminSearch({ paused: true });
    await admin.patchAdminSearch({ ocr: false });
    await admin.patchAdminSearch({ faces_allowed: false, beeld: true });
    for (const what of admin.REBUILD_WHAT) await admin.rebuildSearch(what);
    await admin.failedSearch();
    await admin.failedSearch("c1");
    await admin.retrySearch();
    await admin.wipeBeeld();

    const A = "/api/v1/admin/search";
    expect(request.mock.calls).toEqual([
      ["GET", A],
      ["PATCH", A, { paused: true }],
      ["PATCH", A, { ocr: false }],
      ["PATCH", A, { faces_allowed: false, beeld: true }],
      ["POST", `${A}/rebuild`, { what: "names" }],
      ["POST", `${A}/rebuild`, { what: "text" }],
      ["POST", `${A}/rebuild`, { what: "geo" }],
      ["POST", `${A}/rebuild`, { what: "hash" }],
      ["POST", `${A}/rebuild`, { what: "ocr" }],
      ["POST", `${A}/rebuild`, { what: "faces" }],
      ["POST", `${A}/rebuild`, { what: "beeld" }],
      ["GET", `${A}/failed`],
      ["GET", `${A}/failed?cursor=c1`],
      ["POST", `${A}/retry`],
      ["DELETE", `${A}/beeld`],
    ]);
  });
});
