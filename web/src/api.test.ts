import { describe, expect, it } from "vitest";
import { Api, ApiError, formatBytes } from "./api";

const user = { id: "u", email: "j@x.nl", display_name: "J", role: "admin", quota_bytes: 0, used_bytes: 0, root_id: "r" };
const tokens = { access_token: "A1", refresh_token: "R", expires_in: 900, user };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("Api", () => {
  it("stuurt het access-token mee na inloggen", async () => {
    const seen: string[] = [];
    const api = new Api(async (input, init) => {
      const url = String(input);
      seen.push(`${url} ${new Headers(init?.headers).get("Authorization") ?? "-"}`);
      return url.endsWith("/login") ? json(tokens) : json(user);
    });
    await api.login("j@x.nl", "geheim");
    await api.me();
    expect(seen[1]).toBe("/api/v1/auth/me Bearer A1");
  });

  it("ververst één keer bij een 401 en probeert opnieuw", async () => {
    let meCalls = 0;
    const api = new Api(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/login")) return json(tokens);
      if (url.endsWith("/refresh")) return json({ ...tokens, access_token: "A2" });
      meCalls++;
      const auth = new Headers(init?.headers).get("Authorization");
      return auth === "Bearer A2" ? json(user) : json({ detail: "verlopen" }, 401);
    });
    await api.login("j@x.nl", "geheim");
    await expect(api.me()).resolves.toMatchObject({ email: "j@x.nl" });
    expect(meCalls).toBe(2);
  });

  it("meldt uitloggen als verversen niet lukt", async () => {
    let loggedOut = false;
    const api = new Api(async (input) => {
      const url = String(input);
      if (url.endsWith("/login")) return json(tokens);
      return json({ detail: "weg" }, 401);
    });
    api.onLoggedOut = () => (loggedOut = true);
    await api.login("j@x.nl", "geheim");
    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    expect(loggedOut).toBe(true);
    expect(api.loggedIn).toBe(false);
  });

  it("geeft de foutmelding van de API door", async () => {
    const api = new Api(async () => json({ detail: "Er bestaat al iets met deze naam" }, 409));
    await expect(api.mkdir("r", "x")).rejects.toThrow("Er bestaat al iets met deze naam");
  });
});

describe("formatBytes", () => {
  it("rekent om naar leesbare eenheden", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1,5 KB");
    expect(formatBytes(700 * 1024 ** 3)).toBe("700,0 GB");
  });
});
