import { describe, expect, it } from "vitest";
import { Api, ApiError, errorFromBody, formatBytes, tusMetadata } from "./api";
import { type SendChunk, uploadFile } from "./upload";

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

  it("HEAD zonder body: 409 is een bezette naam, 413 het quotum", async () => {
    let status = 409;
    const api = new Api(async () => new Response(null, { status }));
    await expect(api.tusStatus("/api/v1/tus/x")).rejects.toMatchObject({ status: 409, code: "name_taken" });
    status = 413;
    await expect(api.tusStatus("/api/v1/tus/x")).rejects.toMatchObject({ status: 413, message: "Onvoldoende quotum" });
    status = 404;
    await expect(api.tusStatus("/api/v1/tus/x")).rejects.toMatchObject({ status: 404 });
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

  it("een mislukte refresh tijdens een upload beëindigt de sessie", async () => {
    let loggedOut = 0;
    const seen: string[] = [];
    const api = new Api(async (input, init) => {
      const url = String(input);
      seen.push(`${url} ${new Headers(init?.headers).get("Authorization") ?? "-"}`);
      if (url.endsWith("/login")) return json(tokens);
      if (url.endsWith("/refresh")) return json({ detail: "Ongeldig refresh-token" }, 401);
      if (url === "/api/v1/tus") return new Response(null, { status: 201, headers: { Location: "/api/v1/tus/1" } });
      return json({ detail: "Niet ingelogd" }, 401);
    });
    api.onLoggedOut = () => loggedOut++;
    await api.login("j@x.nl", "geheim");
    // Het access-token verloopt midden in de upload, en de login is intussen ingetrokken
    const expired: SendChunk = async () => {
      throw new ApiError(401, "Token verlopen");
    };
    await expect(uploadFile(api, "p", new File(["x"], "a.txt"), () => {}, { send: expired })).rejects.toThrow(
      "Token verlopen",
    );
    expect(loggedOut).toBe(1);
    expect(api.loggedIn).toBe(false);
    expect(seen.filter((s) => s.startsWith("/api/v1/tus "))).toEqual(["/api/v1/tus Bearer A1"]);
  });

  it("zonder sessie (bij het openen van de pagina) is een mislukte refresh geen uitloggen", async () => {
    let loggedOut = false;
    const api = new Api(async () => json({ detail: "Geen refresh-token" }, 401));
    api.onLoggedOut = () => (loggedOut = true);
    expect(await api.refresh()).toBeNull();
    expect(loggedOut).toBe(false);
  });

  it("gelijktijdige 401's verversen maar één keer", async () => {
    let refreshes = 0;
    const api = new Api(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/login")) return json(tokens);
      if (url.endsWith("/refresh")) {
        refreshes++;
        // Elk refresh-token werkt één keer; een tweede keer trekt de server de login in
        return refreshes === 1 ? json({ ...tokens, access_token: "A2" }) : json({ detail: "al gebruikt" }, 401);
      }
      const auth = new Headers(init?.headers).get("Authorization");
      return auth === "Bearer A2" ? json(user) : json({ detail: "verlopen" }, 401);
    });
    await api.login("j@x.nl", "geheim");
    await Promise.all([api.me(), api.me(), api.refresh()]);
    expect(refreshes).toBe(1);
    expect(api.loggedIn).toBe(true);
  });

  it("meldt het als de server eerst 2FA wil", async () => {
    let asked = 0;
    const api = new Api(async () =>
      json({ detail: { message: "Zet eerst 2FA aan voor je account.", code: "totp_setup_required" } }, 403),
    );
    api.onSetupRequired = () => asked++;
    await expect(api.root()).rejects.toMatchObject({ code: "totp_setup_required" });
    expect(asked).toBe(1);
  });

  it("stuurt bij 2FA aanzetten ook het wachtwoord mee", async () => {
    let body = "";
    const api = new Api(async (_input, init) => {
      body = String(init?.body);
      return json({ recovery_codes: ["abcde-fghjk"] });
    });
    expect(await api.totpEnable("123456", "geheim")).toEqual(["abcde-fghjk"]);
    expect(JSON.parse(body)).toEqual({ code: "123456", password: "geheim" });
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
