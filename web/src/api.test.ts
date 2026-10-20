import { describe, expect, it } from "vitest";
import { Api, ApiError, errorFromBody, formatBytes, tusMetadata } from "./api";

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

describe("fouten en metadata", () => {
  it("leest een fout met code (2FA nodig)", () => {
    const e = errorFromBody(401, { detail: { message: "Vul de code in", code: "totp_required" } });
    expect(e.message).toBe("Vul de code in");
    expect(e.code).toBe("totp_required");
    expect(errorFromBody(422, { detail: [{ msg: "Ongeldig domein" }] }).message).toBe("Ongeldig domein");
    expect(errorFromBody(500, null).message).toBe("Fout 500");
  });

  it("geeft 2FA-code mee bij inloggen zonder uit te loggen bij een 401", async () => {
    let body = "";
    let loggedOut = false;
    const api = new Api(async (_input, init) => {
      body = String(init?.body);
      return json({ detail: { message: "Code nodig", code: "totp_required" } }, 401);
    });
    api.onLoggedOut = () => (loggedOut = true);
    await expect(api.login("j@x.nl", "geheim", "123456")).rejects.toMatchObject({ code: "totp_required" });
    expect(JSON.parse(body).code).toBe("123456");
    expect(loggedOut).toBe(false);
  });

  it("codeert tus-metadata als base64 van UTF-8", () => {
    // "één.txt" in UTF-8 is c3 a9 c3 a9 6e 2e 74 78 74
    expect(tusMetadata({ filename: "één.txt", parent: "p" })).toBe("filename w6nDqW4udHh0,parent cA==");
  });
});

describe("formatBytes", () => {
  it("rekent om naar leesbare eenheden", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1,5 KB");
    expect(formatBytes(700 * 1024 ** 3)).toBe("700,0 GB");
  });
});
