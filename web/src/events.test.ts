import { describe, expect, it } from "vitest";
import { Api, ApiError } from "./api";
import { affectsFolder, LiveEvents, type ServerEvent, SseParser, type StreamApi } from "./events";

/** Een stroom zoals fetch die geeft: write() stuurt tekst, close() is de server die sluit;
 * afbreken via het signaal laat het lezen mislukken, net als bij een echte fetch. */
function fakeStream(signal?: AbortSignal | null) {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const encoder = new TextEncoder();
  let open = true;
  const fail = (reason: unknown) => {
    if (open) controller.error(reason);
    open = false;
  };
  signal?.addEventListener("abort", () => fail(new DOMException("Afgebroken", "AbortError")));
  return {
    response: new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    write: (text: string) => controller.enqueue(encoder.encode(text)),
    close: () => {
      if (open) controller.close();
      open = false;
    },
    fail,
  };
}

type Fake = ReturnType<typeof fakeStream>;

async function until(check: () => boolean, ms = 2000): Promise<void> {
  const start = Date.now();
  while (!check()) {
    if (Date.now() - start > ms) throw new Error("Duurde te lang");
    await new Promise((r) => setTimeout(r, 1));
  }
}

function setup(connect?: (signal: AbortSignal, n: number) => Promise<Response>) {
  const streams: Fake[] = [];
  const api: StreamApi = {
    eventStream: (signal) => {
      if (connect) return connect(signal, streams.length);
      const s = fakeStream(signal);
      streams.push(s);
      return Promise.resolve(s.response);
    },
  };
  const sleeps: number[] = [];
  const live = new LiveEvents(api, {
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    random: () => 0.5,
  });
  const seen: ServerEvent[] = [];
  live.subscribe((e) => seen.push(e));
  return { live, streams, sleeps, seen };
}

describe("SseParser", () => {
  it("leest gebeurtenissen, ook in losse stukken", () => {
    const p = new SseParser();
    expect(p.push("retry: 5000\n\nevent: node.cre")).toEqual([]);
    expect(p.retry).toBe(5000);
    expect(p.push('ated\ndata: {"id":"a"}\n\n: ping\n\n')).toEqual([{ type: "node.created", data: { id: "a" } }]);
    // \r\n verdeeld over twee stukken
    expect(p.push("event: x\r")).toEqual([]);
    expect(p.push("\ndata: {}\r\n\r\n")).toEqual([{ type: "x", data: {} }]);
    // Meer data-regels horen bij elkaar; zonder event heet het "message"
    expect(p.push('data: {"a":\ndata: 1}\n\n')).toEqual([{ type: "message", data: { a: 1 } }]);
    // Geen JSON, of geen data: overslaan
    expect(p.push("event: y\ndata: geen json\n\nevent: z\n\n")).toEqual([]);
  });
});

describe("LiveEvents", () => {
  it("geeft gebeurtenissen door en verbindt opnieuw als de server sluit", async () => {
    const { live, streams, sleeps, seen } = setup();
    live.start();
    live.start(); // tweede keer doet niets
    await until(() => streams.length === 1);
    streams[0].write('retry: 2000\n\nevent: node.created\ndata: {"id":"a","parent_id":"p"}\n\n');
    await until(() => seen.length === 2);
    expect(seen).toEqual([
      { type: "connected", data: {} },
      { type: "node.created", data: { id: "a", parent_id: "p" } },
    ]);
    streams[0].close(); // bijv. token verlopen: na de retry van de server opnieuw
    await until(() => streams.length === 2);
    expect(sleeps).toEqual([2000]);
    await until(() => seen.length === 3);
    expect(seen[2].type).toBe("connected"); // wie luistert, laadt opnieuw
    live.stop();
    expect(live.running).toBe(false);
  });

  it("wacht steeds langer als de verbinding niet lukt", async () => {
    let attempts = 0;
    const { live, sleeps, seen } = setup(async (signal) => {
      attempts++;
      if (attempts <= 7) throw new TypeError("netwerkfout");
      return fakeStream(signal).response;
    });
    live.start();
    await until(() => seen.length === 1);
    expect(sleeps).toEqual([750, 1500, 3000, 6000, 12000, 22500, 22500]); // hooguit 30 s, met spreiding
    live.stop();
  });

  it("stopt bij 401 of 403: dan is er geen sessie (meer)", async () => {
    for (const status of [401, 403]) {
      const { live, sleeps } = setup(async () => {
        throw new ApiError(status, "Niet ingelogd");
      });
      live.start();
      await until(() => !live.running);
      expect(sleeps).toEqual([]);
    }
  });

  it("een stroom die stilvalt wordt afgebroken en opnieuw geopend", async () => {
    const streams: Fake[] = [];
    const live = new LiveEvents(
      {
        eventStream: async (signal) => {
          const s = fakeStream(signal);
          streams.push(s);
          return s.response;
        },
      },
      { stallTimeout: 30, sleep: async () => {}, random: () => 0.5 },
    );
    live.start();
    await until(() => streams.length === 2);
    live.stop();
  });

  it("stop() verbreekt de verbinding en verbindt niet opnieuw", async () => {
    const { live, streams, sleeps } = setup();
    live.start();
    await until(() => streams.length === 1);
    live.stop();
    await new Promise((r) => setTimeout(r, 20));
    expect(streams.length).toBe(1);
    expect(sleeps).toEqual([]);
    live.start(); // en daarna weer opnieuw kunnen beginnen (StrictMode doet dat ook)
    await until(() => streams.length === 2);
    live.stop();
  });
});

const user = { id: "u", email: "j@x.nl", display_name: "J", role: "admin", quota_bytes: 0, used_bytes: 0, root_id: "r" };
const tokens = { access_token: "A1", refresh_token: "R", expires_in: 900, user };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("LiveEvents met de echte Api", () => {
  it("verlopen token: verversen en verder, met het token in de header", async () => {
    const calls: string[] = [];
    const streams: Fake[] = [];
    const api = new Api(async (input, init) => {
      const url = String(input);
      const auth = new Headers(init?.headers).get("Authorization");
      calls.push(`${url} ${auth ?? "-"}`);
      if (url.endsWith("/login")) return json(tokens);
      if (url.endsWith("/refresh")) return json({ ...tokens, access_token: "A2" });
      if (auth !== "Bearer A2") return json({ detail: "Ongeldig of verlopen token" }, 401);
      const s = fakeStream(init?.signal);
      streams.push(s);
      return s.response;
    });
    await api.login("j@x.nl", "geheim");
    const live = new LiveEvents(api, { sleep: async () => {} });
    const seen: ServerEvent[] = [];
    live.subscribe((e) => seen.push(e));
    live.start();
    await until(() => seen.length === 1);
    expect(calls).toEqual([
      "/api/v1/auth/login -",
      "/api/v1/events Bearer A1",
      "/api/v1/auth/refresh -",
      "/api/v1/events Bearer A2",
    ]);
    expect(calls.some((c) => c.includes("token="))).toBe(false); // nooit in het adres
    live.stop();
  });

  it("verversen mislukt: uitgelogd, en de stroom stopt vanzelf", async () => {
    let loggedOut = 0;
    const api = new Api(async (input) => {
      const url = String(input);
      if (url.endsWith("/login")) return json(tokens);
      return json({ detail: "Ongeldig refresh-token" }, 401);
    });
    api.onLoggedOut = () => loggedOut++;
    await api.login("j@x.nl", "geheim");
    const live = new LiveEvents(api, { sleep: async () => {} });
    live.start();
    await until(() => !live.running);
    // Api.raw meldt het uitloggen soms twee keer (bij renew en bij de 401); App vangt dat op
    expect(loggedOut).toBeGreaterThan(0);
  });
});

describe("affectsFolder", () => {
  const event = (type: string, data: Record<string, unknown>) => ({ type, data });
  it("alleen wijzigingen in, naar of van de getoonde map", () => {
    expect(affectsFolder(event("node.created", { id: "x", parent_id: "map" }), "map")).toBe(true);
    expect(affectsFolder(event("node.changed", { id: "x", parent_id: "elders", old_parent_id: "map" }), "map")).toBe(
      true,
    );
    expect(affectsFolder(event("node.changed", { id: "map", parent_id: "boven" }), "map")).toBe(true);
    expect(affectsFolder(event("node.deleted", { id: "x", parent_id: "elders" }), "map")).toBe(false);
    expect(affectsFolder(event("preview.ready", { node_ids: ["map"] }), "map")).toBe(false);
    expect(affectsFolder(event("connected", {}), "map")).toBe(true); // misschien iets gemist
    expect(affectsFolder(event("connected", {}), undefined)).toBe(false);
  });
});
