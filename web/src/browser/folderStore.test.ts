import { describe, expect, it } from "vitest";
import { ApiError, type FileNode } from "../api";
import type { ServerEvent } from "../events";
import type { Page, PageOptions } from "./api";
import { FolderStore, type FolderStoreDeps } from "./folderStore";
import { dirNode, fileNode } from "./testNodes";

const folder = dirNode({ id: "map", parent_id: "root", name: "Map" });
const root = dirNode({ id: "root", parent_id: null, name: "" });

/** Een server in het geheugen: de kinderen van "map" in servervolgorde, pagina's met een cursor. */
function fakeServer(children: FileNode[]) {
  const state = {
    children: [...children],
    /** Knopen buiten de map (de map zelf, het pad erboven) die lookup ook kent */
    others: [] as FileNode[],
    pages: [] as PageOptions[],
    lookups: [] as string[][],
    fail: null as unknown,
  };
  const sorted = () =>
    [...state.children].sort((a, b) =>
      a.kind !== b.kind ? (a.kind < b.kind ? -1 : 1) : a.name < b.name ? -1 : a.name > b.name ? 1 : 0,
    );
  const page = async (_id: string | null, o: PageOptions): Promise<Page> => {
    state.pages.push(o);
    if (state.fail) throw state.fail;
    const all = sorted();
    const start = o.after ? all.findIndex((n) => n.id === o.after) + 1 : 0;
    const limit = o.limit ?? all.length;
    const part = all.slice(start, start + limit);
    const next = start + limit < all.length ? part[part.length - 1].id : null;
    return { folder, path: [root, folder], children: part, next, total: all.length };
  };
  const lookup = async (ids: string[]): Promise<FileNode[]> => {
    state.lookups.push(ids);
    return [...state.children, ...state.others].filter((n) => ids.includes(n.id));
  };
  return { state, page, lookup };
}

/** Uitgestelde taken met de hand afspelen (idle en later). */
function clock() {
  const tasks: { run: () => void; ms: number }[] = [];
  return {
    tasks,
    idle: (cb: () => void) => {
      const task = { run: cb, ms: 0 };
      tasks.push(task);
      return () => tasks.splice(tasks.indexOf(task), 1);
    },
    later: (cb: () => void, ms: number) => {
      const task = { run: cb, ms };
      tasks.push(task);
      return () => {
        const i = tasks.indexOf(task);
        if (i >= 0) tasks.splice(i, 1);
      };
    },
    /** Alles wat klaarstaat (ook wat daardoor weer klaar komt), met de beloftes ertussen. */
    async flush(): Promise<void> {
      for (let round = 0; round < 50; round++) {
        await settle();
        const task = tasks.shift();
        if (!task) return;
        task.run();
      }
    },
  };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

function store(children: FileNode[], o: { preloaded?: Page } = {}) {
  const server = fakeServer(children);
  const c = clock();
  const deps: FolderStoreDeps = {
    page: server.page,
    lookup: server.lookup,
    idle: c.idle,
    later: c.later,
    preloaded: () => (o.preloaded ? Promise.resolve(o.preloaded) : null),
  };
  const s = new FolderStore("map", deps);
  return { s, server: server.state, clock: c };
}

function files(n: number): FileNode[] {
  return Array.from({ length: n }, (_, i) => fileNode({ id: `f${String(i).padStart(5, "0")}`, name: `f${String(i).padStart(5, "0")}` }));
}

const ev = (type: string, data: Record<string, unknown>): ServerEvent => ({ type, data });

describe("pagina's", () => {
  it("eerst 200, de rest in pagina's van 1.000 als er tijd is", async () => {
    const { s, server, clock } = store(files(2500));
    s.start();
    await settle();
    let snap = s.getSnapshot();
    expect(snap.status).toBe("ready");
    expect(snap.ids.length).toBe(200);
    expect(snap.total).toBe(2500);
    expect(snap.complete).toBe(false);
    expect(server.pages[0]).toEqual({ limit: 200 });
    await clock.flush();
    snap = s.getSnapshot();
    expect(snap.ids.length).toBe(2500);
    expect(snap.complete).toBe(true);
    expect(server.pages.slice(1).map((p) => p.limit)).toEqual([1000, 1000, 1000]);
    expect(new Set(snap.ids).size).toBe(2500);
  });

  it("neemt een vooraf opgehaalde eerste pagina over", async () => {
    const all = files(3);
    const preloaded: Page = { folder, path: [root, folder], children: all, next: null, total: 3 };
    const { s, server } = store(all, { preloaded });
    s.start();
    await settle();
    expect(s.getSnapshot().ids).toEqual(all.map((n) => n.id));
    expect(server.pages).toEqual([]);
  });

  it("een 404: de map is weg", async () => {
    const { s, server } = store([]);
    server.fail = new ApiError(404, "Niet gevonden", "not_found");
    s.start();
    await settle();
    expect(s.getSnapshot().status).toBe("gone");
  });

  it("een andere fout bij de eerste pagina: error", async () => {
    const { s, server } = store([]);
    server.fail = new ApiError(0, "Geen verbinding", "network");
    s.start();
    await settle();
    expect(s.getSnapshot().status).toBe("error");
  });
});

describe("live bijwerken", () => {
  async function ready(children: FileNode[]) {
    const t = store(children);
    t.s.start();
    await t.clock.flush();
    return t;
  }

  it("nieuw: ids 150 ms verzamelen, één lookup, op de goede plek", async () => {
    const { s, server, clock } = await ready([fileNode({ id: "a", name: "a" }), fileNode({ id: "c", name: "c" })]);
    server.children.push(fileNode({ id: "b", name: "b" }), dirNode({ id: "d", name: "zz" }));
    s.onEvent(ev("node.created", { id: "b", parent_id: "map" }));
    s.onEvent(ev("node.created", { ids: ["d"], parent_id: "map" }));
    expect(clock.tasks.map((t) => t.ms)).toEqual([150]);
    await clock.flush();
    expect(server.lookups).toEqual([["b", "d"]]);
    expect(s.getSnapshot().ids).toEqual(["d", "a", "b", "c"]);
    expect(s.getSnapshot().total).toBe(4);
  });

  it("weg verplaatst, verwijderd of in de prullenbak: eruit", async () => {
    const a = fileNode({ id: "a", name: "a" });
    const b = fileNode({ id: "b", name: "b" });
    const c = fileNode({ id: "c", name: "c" });
    const { s, server, clock } = await ready([a, b, c]);
    // a verplaatst, b in de prullenbak (lookup laat hem weg), c met deleted_at
    server.children = [{ ...a, parent_id: "elders" }, { ...c, deleted_at: "2026-10-02T00:00:00Z" }];
    s.onEvent(ev("node.moved", { ids: ["a"], parent_id: "elders", old_parent_id: "map" }));
    s.onEvent(ev("node.trashed", { id: "b", parent_id: "map" }));
    s.onEvent(ev("node.trashed", { id: "c", parent_id: "map" }));
    await clock.flush();
    expect(s.getSnapshot().ids).toEqual([]);
    expect(s.getSnapshot().total).toBe(0);
  });

  it("hernoemd: andere plek in de lijst", async () => {
    const { s, server, clock } = await ready([fileNode({ id: "a", name: "a" }), fileNode({ id: "b", name: "b" })]);
    server.children[0] = fileNode({ id: "a", name: "z" });
    s.onEvent(ev("node.updated", { id: "a", parent_id: "map" }));
    await clock.flush();
    expect(s.getSnapshot().ids).toEqual(["b", "a"]);
    expect(s.getSnapshot().nodes.get("a")?.name).toBe("z");
  });

  it("een gebeurtenis in een andere map raakt de lijst niet", async () => {
    const { s, server, clock } = await ready([fileNode({ id: "a", name: "a" })]);
    s.onEvent(ev("node.created", { id: "x", parent_id: "elders" }));
    await clock.flush();
    expect(server.lookups).toEqual([]);
  });

  it("connected en tags.changed: alles opnieuw, de oude lijst blijft tot dan staan", async () => {
    const { s, server, clock } = await ready([fileNode({ id: "a", name: "a" })]);
    server.children.push(fileNode({ id: "b", name: "b" }));
    const before = server.pages.length;
    s.onEvent(ev("connected", {}));
    expect(s.getSnapshot().ids).toEqual(["a"]);
    await clock.flush();
    expect(server.pages.length).toBe(before + 1);
    expect(s.getSnapshot().ids).toEqual(["a", "b"]);
    s.onEvent(ev("tags.changed", {}));
    await clock.flush();
    expect(server.pages.length).toBe(before + 2);
  });

  it("een deling verandert: alleen opnieuw in een gedeelde map", async () => {
    const { s, server, clock } = await ready([]);
    const before = server.pages.length;
    s.onEvent(ev("share.created", { id: "x" }), { shared: false });
    await clock.flush();
    expect(server.pages.length).toBe(before);
    s.onEvent(ev("share.deleted", { id: "x" }), { shared: true });
    await clock.flush();
    expect(server.pages.length).toBe(before + 1);
  });

  it("iets na wat er geladen is, komt pas met een volgende pagina", async () => {
    const t = store(files(1500));
    t.s.start();
    await settle(); // alleen de eerste 200
    t.server.children.push(fileNode({ id: "zzz", name: "zzz" }));
    t.s.onEvent(ev("node.created", { id: "zzz", parent_id: "map" }));
    // eerst de verzamelde lookup, dan pas de pagina's
    const flushTask = t.clock.tasks.find((x) => x.ms === 150);
    t.clock.tasks.splice(t.clock.tasks.indexOf(flushTask!), 1);
    flushTask!.run();
    await settle();
    expect(t.s.getSnapshot().ids).not.toContain("zzz");
    await t.clock.flush();
    expect(t.s.getSnapshot().ids[t.s.getSnapshot().ids.length - 1]).toBe("zzz");
    expect(t.s.getSnapshot().ids.length).toBe(1501);
  });

  it("eigen acties: upsert en remove zonder de server", async () => {
    const { s, server } = await ready([fileNode({ id: "a", name: "a" })]);
    const lookups = server.lookups.length;
    s.upsert([dirNode({ id: "n", name: "Nieuw", parent_id: "map" })]);
    expect(s.getSnapshot().ids).toEqual(["n", "a"]);
    s.upsert([fileNode({ id: "elders", name: "x", parent_id: "andere" })]);
    expect(s.getSnapshot().ids).toEqual(["n", "a"]);
    s.remove(["a"]);
    expect(s.getSnapshot().ids).toEqual(["n"]);
    expect(server.lookups.length).toBe(lookups);
  });

  it("de map zelf hernoemd: de kop werkt bij; weg: herladen (404 = weg)", async () => {
    const { s, server, clock } = await ready([]);
    const renamed = { ...folder, name: "Andere naam" };
    server.others = [root, renamed];
    s.onEvent(ev("node.updated", { id: "map", parent_id: "root" }));
    await clock.flush();
    expect(s.getSnapshot().folder?.name).toBe("Andere naam");
    server.others = [root];
    server.fail = new ApiError(404, "Niet gevonden", "not_found");
    s.onEvent(ev("node.trashed", { id: "map", parent_id: "root" }));
    await clock.flush();
    expect(s.getSnapshot().status).toBe("gone");
  });

  it("na opruimen doet hij niets meer", async () => {
    const { s, server, clock } = await ready([]);
    s.dispose();
    s.onEvent(ev("node.created", { id: "x", parent_id: "map" }));
    await clock.flush();
    expect(server.lookups).toEqual([]);
  });
});
