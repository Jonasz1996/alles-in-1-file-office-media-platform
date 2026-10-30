import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, NETWORK_MESSAGE } from "../api";
import { preloadFolder, takePreloaded } from "./preload";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("preloadFolder", () => {
  it("offline bij het opstarten (refresh gooit): null, geen onafgehandelde belofte", async () => {
    const refresh = vi.spyOn(api, "refresh").mockRejectedValue(new ApiError(0, NETWORK_MESSAGE, "network"));
    const request = vi.spyOn(api, "request");
    preloadFolder(null);
    const page = takePreloaded(null);
    expect(page).not.toBeNull();
    await expect(page).resolves.toBeNull();
    expect(refresh).toHaveBeenCalledOnce();
    expect(request).not.toHaveBeenCalled();
  });
});
