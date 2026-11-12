// De rij (queue.ts): de pure bewerkingen, wat er uit sessionStorage terugkomt (niet te
// vertrouwen, en alleen voor dezelfde gebruiker) en dat er geen hoes in de opslag komt.

import { describe, expect, it } from "vitest";
import {
  appendItems,
  EMPTY,
  moveItem,
  parseQueue,
  QUEUE_KEY,
  QUEUE_MAX,
  type QueueInput,
  QueueStore,
  removeAt,
  replaceQueue,
  serializeQueue,
} from "./queue";

function track(n: number, extra: Partial<QueueInput> = {}): QueueInput {
  return {
    node_id: `n${n}`,
    title: `Nummer ${n}`,
    artist: "ABBA",
    album: "Gold",
    album_id: "a1",
    duration_ms: 1000 * n,
    ...extra,
  };
}

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string): string | null {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.data.set(k, v);
  }
  removeItem(k: string): void {
    this.data.delete(k);
  }
}

describe("de bewerkingen", () => {
  it("een nieuwe rij begint op start (binnen de grenzen) en geeft elk nummer een eigen uid", () => {
    const q = replaceQueue([track(1), track(2), track(1)], 7);
    expect(q.index).toBe(2);
    expect(q.position).toBe(0);
    expect(new Set(q.items.map((x) => x.uid)).size).toBe(3);
    expect(replaceQueue([], 0)).toBe(EMPTY);
  });

  it("achteraan erbij, tot QUEUE_MAX", () => {
    const q = replaceQueue([track(1)]);
    const { state, added } = appendItems(q, [track(2), track(3)]);
    expect(added).toBe(2);
    expect(state.items.map((x) => x.node_id)).toEqual(["n1", "n2", "n3"]);
    expect(state.index).toBe(0);
    const full = replaceQueue(Array.from({ length: QUEUE_MAX }, (_, i) => track(i)));
    const more = appendItems(full, [track(1)]);
    expect(more.added).toBe(0);
    expect(more.state).toBe(full);
    // In een lege rij wordt het eerste nummer het huidige
    expect(appendItems(EMPTY, [track(9)]).state.index).toBe(0);
  });

  it("weghalen houdt het huidige nummer het huidige", () => {
    const q = { ...replaceQueue([track(1), track(2), track(3), track(4)], 2), position: 12 };
    expect(removeAt(q, 0).index).toBe(1); // ervoor: de plek schuift mee
    expect(removeAt(q, 0).position).toBe(12);
    expect(removeAt(q, 3).index).toBe(2); // erna: niets
    const now = removeAt(q, 2); // het huidige zelf: het volgende, vooraan
    expect(now.items[now.index].node_id).toBe("n4");
    expect(now.position).toBe(0);
    const last = removeAt({ ...q, index: 3 }, 3);
    expect(last.index).toBe(2);
    expect(removeAt(replaceQueue([track(1)]), 0)).toBe(EMPTY);
    expect(removeAt(q, 9)).toBe(q);
  });

  it("verplaatsen houdt bij welk nummer het huidige is", () => {
    const q = replaceQueue([track(1), track(2), track(3), track(4)], 1); // n2 speelt
    const a = moveItem(q, 1, 3);
    expect(a.items.map((x) => x.node_id)).toEqual(["n1", "n3", "n4", "n2"]);
    expect(a.items[a.index].node_id).toBe("n2");
    const b = moveItem(q, 0, 2);
    expect(b.items[b.index].node_id).toBe("n2");
    const c = moveItem(q, 3, 0);
    expect(c.items[c.index].node_id).toBe("n2");
    expect(moveItem(q, 0, 9)).toBe(q);
  });
});

describe("de opslag", () => {
  it("geen hoes (een ondertekend adres) en geen sid in de opslag", () => {
    const q = replaceQueue([track(1, { cover: "/api/v1/previews/x?sig=geheim" })]);
    const raw = serializeQueue(q, "u1");
    expect(raw).not.toContain("geheim");
    expect(raw).not.toContain("cover");
    const back = parseQueue(raw, "u1");
    expect(back.items[0]).toMatchObject({ node_id: "n1", title: "Nummer 1", artist: "ABBA", duration_ms: 1000 });
    expect(back.items[0].cover).toBeUndefined();
  });

  it("alleen van dezelfde gebruiker", () => {
    const raw = serializeQueue(replaceQueue([track(1)]), "u1");
    expect(parseQueue(raw, "u2")).toBe(EMPTY);
    expect(parseQueue(raw, null)).toBe(EMPTY);
  });

  it("rommel geeft een lege rij of alleen de geldige regels", () => {
    for (const raw of [null, "", "{", "[]", '{"owner":"u1"}', '{"owner":"u1","items":{}}', "null", '"x"']) {
      expect(parseQueue(raw, "u1")).toBe(EMPTY);
    }
    const raw = JSON.stringify({
      owner: "u1",
      index: 99,
      position: -5,
      items: [
        null,
        { node_id: "n1" }, // zonder titel
        { node_id: 5, title: "x" },
        { node_id: "n2", title: "Twee", duration_ms: "lang", uid: "../../x" },
        { node_id: "n3", title: "Drie", duration_ms: 3000, uid: "a-1" },
        { node_id: "n4", title: "Vier", uid: "a-1" }, // dubbele uid
      ],
    });
    const q = parseQueue(raw, "u1");
    expect(q.items.map((x) => x.node_id)).toEqual(["n2", "n3", "n4"]);
    expect(q.items[0].duration_ms).toBeNull();
    expect(q.items[0].uid).not.toBe("../../x");
    expect(new Set(q.items.map((x) => x.uid)).size).toBe(3);
    expect(q.index).toBe(2);
    expect(q.position).toBe(0);
  });

  it("een vaste-seed fuzz: parseQueue gooit nooit en houdt zich aan de grenzen", () => {
    let seed = 20261030;
    const rnd = () => {
      seed = (seed * 1103515245 + 12345) % 2 ** 31;
      return seed / 2 ** 31;
    };
    const pieces = ['{"owner":"u1","items":[', '{"node_id":"n","title":"t"}', ",", "]", "}", "null", '"', "1e999", "-1"];
    for (let i = 0; i < 2000; i++) {
      let s = "";
      const n = Math.floor(rnd() * 12);
      for (let k = 0; k < n; k++) s += pieces[Math.floor(rnd() * pieces.length)];
      const q = parseQueue(s, "u1");
      expect(q.items.length).toBeLessThanOrEqual(QUEUE_MAX);
      if (q.items.length) {
        expect(q.index).toBeGreaterThanOrEqual(0);
        expect(q.index).toBeLessThan(q.items.length);
      }
    }
  });

  it("de store bewaart, laadt per gebruiker en wist", () => {
    const mem = new MemoryStorage();
    const a = new QueueStore(() => mem);
    a.bind("u1");
    a.set(replaceQueue([track(1), track(2)], 1));
    a.setPosition(42.26);
    expect(mem.getItem(QUEUE_KEY)).toContain('"position":42.3');

    const b = new QueueStore(() => mem);
    b.bind("u1");
    expect(b.snapshot().items.map((x) => x.node_id)).toEqual(["n1", "n2"]);
    expect(b.snapshot().index).toBe(1);
    expect(b.snapshot().position).toBeCloseTo(42.3);

    const c = new QueueStore(() => mem);
    c.bind("u2");
    expect(c.snapshot()).toBe(EMPTY);

    b.clear();
    expect(mem.getItem(QUEUE_KEY)).toBeNull();
    expect(b.snapshot()).toBe(EMPTY);
  });

  it("een geblokkeerde opslag: de rij werkt in het geheugen", () => {
    const broken = {
      getItem: () => {
        throw new Error("geblokkeerd");
      },
      setItem: () => {
        throw new Error("vol");
      },
      removeItem: () => {
        throw new Error("geblokkeerd");
      },
    };
    const s = new QueueStore(() => broken);
    s.bind("u1");
    s.set(replaceQueue([track(1)]));
    expect(s.snapshot().items).toHaveLength(1);
    s.clear();
    expect(s.snapshot()).toBe(EMPTY);
  });
});
