import { afterEach, describe, expect, it, vi } from "vitest";
import { Api } from "./api";

const user = { id: "u", email: "j@x.nl", display_name: "J", role: "admin", quota_bytes: 0, used_bytes: 0, root_id: "r" };
const tokens = { access_token: "A1", refresh_token: "R", expires_in: 900, user };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Api met een nep-server die elk verzoek onthoudt als "METHODE pad body". */
async function loggedIn(answer: (method: string, url: string) => Response = () => new Response(null, { status: 204 })) {
  const seen: string[] = [];
  const api = new Api(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.endsWith("/login")) return json(tokens);
    seen.push(`${method} ${url}${init?.body ? ` ${String(init.body)}` : ""}`);
    return answer(method, url);
  });
  await api.login("j@x.nl", "geheim");
  return { api, seen };
}

describe("account", () => {
  it("haalt de sessies op met het access-token", async () => {
    const session = {
      id: "f1",
      device: "Firefox op Windows",
      ip: "192.0.2.7",
      created_at: "2026-10-20T08:00:00+00:00",
      last_active_at: "2026-10-20T09:00:00+00:00",
      current: true,
    };
    let auth = "";
    const api = new Api(async (input, init) => {
      if (String(input).endsWith("/login")) return json(tokens);
      auth = new Headers(init?.headers).get("Authorization") ?? "";
      return json([session]);
    });
    await api.login("j@x.nl", "geheim");
    expect(await api.sessions()).toEqual([session]);
    expect(auth).toBe("Bearer A1");
  });

  it("een andere sessie uitloggen laat deze ingelogd", async () => {
    const { api, seen } = await loggedIn();
    let loggedOut = false;
    api.onLoggedOut = () => (loggedOut = true);
    await api.revokeSession("f2");
    expect(seen).toEqual(["DELETE /api/v1/auth/sessions/f2"]);
    expect(loggedOut).toBe(false);
    expect(api.loggedIn).toBe(true);
  });

  it("de eigen sessie uitloggen is uitloggen, ook het cookie", async () => {
    const { api, seen } = await loggedIn();
    let loggedOut = 0;
    api.onLoggedOut = () => loggedOut++;
    await api.revokeSession("f1", true);
    expect(seen).toEqual(["DELETE /api/v1/auth/sessions/f1", "POST /api/v1/auth/logout"]);
    expect(loggedOut).toBe(1);
    expect(api.loggedIn).toBe(false);
  });

  it("lukt het intrekken niet, dan blijf je ingelogd", async () => {
    const { api } = await loggedIn(() => json({ detail: "Sessie niet gevonden" }, 404));
    let loggedOut = false;
    api.onLoggedOut = () => (loggedOut = true);
    await expect(api.revokeSession("f1", true)).rejects.toThrow("Sessie niet gevonden");
    expect(loggedOut).toBe(false);
    expect(api.loggedIn).toBe(true);
  });

  it("overal anders uitloggen", async () => {
    const { api, seen } = await loggedIn();
    await api.revokeOtherSessions();
    expect(seen).toEqual(["POST /api/v1/auth/sessions/revoke-others"]);
  });

  it("wachtwoord wijzigen stuurt de code alleen mee als die er is", async () => {
    const { api, seen } = await loggedIn();
    await api.changePassword("oud-wachtwoord", "nieuw-wachtwoord", "123456");
    await api.changePassword("oud-wachtwoord", "nieuw-wachtwoord");
    const bodies = seen.map((s) => JSON.parse(s.slice("POST /api/v1/auth/password ".length)));
    expect(seen[0].startsWith("POST /api/v1/auth/password ")).toBe(true);
    expect(bodies).toEqual([
      { current_password: "oud-wachtwoord", new_password: "nieuw-wachtwoord", code: "123456" },
      { current_password: "oud-wachtwoord", new_password: "nieuw-wachtwoord" },
    ]);
  });

  it("geeft de melding en code van de server door bij een fout wachtwoord of een blokkade", async () => {
    let status = 400;
    const locked = { message: "Te veel foute codes. Probeer het over 1 minuut opnieuw.", code: "totp_locked" };
    const { api } = await loggedIn(() =>
      status === 400 ? json({ detail: "Wachtwoord of code klopt niet" }, 400) : json({ detail: locked }, 429),
    );
    await expect(api.changePassword("fout", "nieuw-wachtwoord", "000000")).rejects.toMatchObject({
      status: 400,
      message: "Wachtwoord of code klopt niet",
    });
    status = 429;
    await expect(api.changePassword("goed", "nieuw-wachtwoord", "123456")).rejects.toMatchObject({
      status: 429,
      code: "totp_locked",
    });
    expect(api.loggedIn).toBe(true);
  });

  it("beheerder stelt een wachtwoord in", async () => {
    const { api, seen } = await loggedIn();
    await api.setUserPassword("u2", "tijdelijk-wachtwoord");
    expect(seen).toEqual(['PUT /api/v1/users/u2/password {"new_password":"tijdelijk-wachtwoord"}']);
  });
});

describe("twee tabbladen verversen tegelijk", () => {
  const message = "Je sessie is net in een ander tabblad vernieuwd; probeer het opnieuw";
  const raced = () => json({ detail: { message, code: "refresh_raced" } }, 401);

  afterEach(() => {
    vi.useRealTimers();
  });

  it("wint het andere tabblad, dan even later opnieuw (met het nieuwe cookie)", async () => {
    vi.useFakeTimers();
    let refreshes = 0;
    const api = new Api(async () => (++refreshes === 1 ? raced() : json(tokens)));
    let loggedOut = false;
    api.onLoggedOut = () => (loggedOut = true);
    const result = api.refresh();
    await vi.advanceTimersByTimeAsync(500);
    await expect(result).resolves.toMatchObject({ email: "j@x.nl" });
    expect(refreshes).toBe(2);
    expect(api.loggedIn).toBe(true);
    expect(loggedOut).toBe(false);
  });

  it("blijft het zo, dan stopt het pas na de marge van de server", async () => {
    vi.useFakeTimers();
    const times: number[] = [];
    const api = new Api(async () => {
      times.push(Date.now());
      return raced();
    });
    const result = api.refresh();
    await vi.runAllTimersAsync();
    await expect(result).resolves.toBeNull();
    expect(times).toHaveLength(4);
    // De laatste poging valt na de 5 s van de server; dan trekt die bij hergebruik de login in
    expect(times[3] - times[0]).toBeGreaterThan(5000);
    expect(api.loggedIn).toBe(false);
  });

  it("een gewone 401 probeert het niet opnieuw", async () => {
    let refreshes = 0;
    const api = new Api(async () => {
      refreshes++;
      return json({ detail: "Refresh-token is al gebruikt; log opnieuw in" }, 401);
    });
    expect(await api.refresh()).toBeNull();
    expect(refreshes).toBe(1);
  });
});
