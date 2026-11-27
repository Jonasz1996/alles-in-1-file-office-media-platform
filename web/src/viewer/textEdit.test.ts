// Tekst bewerken in de viewer (TextView.tsx, 0.8.3): wanneer het kan, laden, opslaan met de
// BOM en de regeleinden van het bestand, Ctrl+S, en de vraag bij weggaan met wijzigingen.

import { describe, expect, it, vi } from "vitest";
import { ApiError, type FileNode } from "../api";
import { fileNode } from "../browser/testNodes";
import { backGuard, hasUnsaved, onBeforeUnload, routerIndex, setLeaveGuard, whenMayLeave } from "./leaveGuard";
import {
  canEditText,
  type EditDeps,
  isSaveKey,
  MAX_EDIT_BYTES,
  putContent,
  readText,
  TextEditSession,
  writeText,
} from "./textEdit";

const enc = (s: string) => new TextEncoder().encode(s);
const dec = (b: Uint8Array) => new TextDecoder("utf-8", { ignoreBOM: true }).decode(b);
const BOM = new Uint8Array([0xef, 0xbb, 0xbf]);

function withBom(s: string): Uint8Array {
  const body = enc(s);
  const out = new Uint8Array(body.length + 3);
  out.set(BOM, 0);
  out.set(body, 3);
  return out;
}

async function blobBytes(b: Blob): Promise<Uint8Array> {
  return new Uint8Array(await b.arrayBuffer());
}

function session(content: Uint8Array, save?: EditDeps["save"]) {
  const saved: Blob[] = [];
  const deps = {
    load: vi.fn(async () => content),
    save: vi.fn(
      save ??
        (async (body: Blob) => {
          saved.push(body);
          return fileNode({ id: "f1", name: "notities.txt", size: body.size });
        }),
    ),
    errorText: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  };
  return { s: new TextEditSession(deps), deps, saved };
}

describe("wanneer bewerken kan", () => {
  const file = { size: 10 };

  it("ingelogd met schrijfrecht (of van jezelf), geen versie, niet in de prullenbak, hooguit 5 MB", () => {
    expect(canEditText(file, "user")).toBe(true);
    expect(canEditText({ ...file, permission: "owner" }, "user")).toBe(true);
    expect(canEditText({ ...file, permission: "write" }, "user")).toBe(true);
    expect(canEditText({ size: MAX_EDIT_BYTES }, "user")).toBe(true);
  });

  it.each([
    ["via een publieke link", file, "public"],
    ["alleen leesrecht", { ...file, permission: "read" as const }, "user"],
    ["een eerdere versie", { ...file, version: { id: "v1" } }, "user"],
    ["in de prullenbak", { ...file, deleted_at: "2026-11-01T00:00:00Z" }, "user"],
    ["groter dan 5 MB", { size: MAX_EDIT_BYTES + 1 }, "user"],
  ])("niet: %s", (_why, target, kind) => {
    expect(canEditText(target, kind)).toBe(false);
  });
});

describe("lezen en schrijven met BOM en regeleinden", () => {
  it("LF blijft LF, zonder BOM", () => {
    const read = readText(enc("een\ntwee\n"));
    expect(read).toEqual({ text: "een\ntwee\n", format: { bom: false, crlf: false } });
    expect(dec(writeText("een\ntwee\ndrie\n", { bom: false, crlf: false }))).toBe("een\ntwee\ndrie\n");
  });

  it("CRLF: in het veld \\n, bij opslaan weer CRLF (ook voor nieuwe regels)", () => {
    const read = readText(enc("een\r\ntwee\r\n"));
    expect(read).toEqual({ text: "een\ntwee\n", format: { bom: false, crlf: true } });
    const out = writeText(`${read?.text}drie\n`, read!.format);
    expect(dec(out)).toBe("een\r\ntwee\r\ndrie\r\n");
  });

  it("een BOM blijft, en komt niet in het veld", () => {
    const read = readText(withBom("hé\r\n"));
    expect(read).toEqual({ text: "hé\n", format: { bom: true, crlf: true } });
    const out = writeText("hé daar\n", read!.format);
    expect([...out.subarray(0, 3)]).toEqual([...BOM]);
    expect(dec(out.subarray(3))).toBe("hé daar\r\n");
  });

  it("gemengd: de meerderheid wint; een leeg bestand is LF zonder BOM", () => {
    expect(readText(enc("a\r\nb\nc\n"))?.format.crlf).toBe(false);
    expect(readText(enc("a\r\nb\r\nc\n"))?.format.crlf).toBe(true);
    expect(readText(new Uint8Array(0))).toEqual({ text: "", format: { bom: false, crlf: false } });
  });

  it("geen UTF-8 (Windows-1252): niet te bewerken", () => {
    expect(readText(new Uint8Array([0x63, 0x61, 0x66, 0xe9]))).toBeNull();
  });

  it("een losse \\r in het veld wordt een regeleinde zoals de rest", () => {
    expect(dec(writeText("a\rb", { bom: false, crlf: false }))).toBe("a\nb");
  });
});

describe("Ctrl+S", () => {
  const key = (k: string, mods: Partial<{ ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }>) => ({
    key: k,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    altKey: false,
    ...mods,
  });

  it("Ctrl+S, en op een Mac Cmd+S; niet S alleen of Ctrl+Shift+S", () => {
    expect(isSaveKey(key("s", { ctrlKey: true }), false)).toBe(true);
    expect(isSaveKey(key("S", { ctrlKey: true }), false)).toBe(true);
    expect(isSaveKey(key("s", { metaKey: true }), true)).toBe(true);
    expect(isSaveKey(key("s", {}), false)).toBe(false);
    expect(isSaveKey(key("s", { ctrlKey: true, shiftKey: true }), false)).toBe(false);
  });
});

describe("bewerken", () => {
  it("laadt de hele tekst, houdt bij wat veranderde, en slaat op met CRLF en BOM van het bestand", async () => {
    const { s, saved, deps } = session(withBom("een\r\ntwee\r\n"));
    expect(s.getState().phase).toBe("reading");
    expect(await s.start()).toBe(true);
    expect(deps.load).toHaveBeenCalledTimes(1);
    expect(s.getState()).toMatchObject({ phase: "editing", text: "een\ntwee\n", saved: "een\ntwee\n" });
    expect(s.dirty()).toBe(false);
    // Zonder wijzigingen: niets op te slaan
    expect(await s.save()).toBeNull();
    expect(deps.save).not.toHaveBeenCalled();

    s.change("een\ntwee\ndrie\n");
    expect(s.dirty()).toBe(true);
    const done = await s.save();
    expect(done?.node.name).toBe("notities.txt");
    expect(saved).toHaveLength(1);
    const bytes = await blobBytes(saved[0]);
    expect([...bytes.subarray(0, 3)]).toEqual([...BOM]);
    expect(dec(bytes.subarray(3))).toBe("een\r\ntwee\r\ndrie\r\n");
    expect([...(done?.bytes ?? [])]).toEqual([...bytes]);
    expect(s.dirty()).toBe(false);
    expect(s.getState().phase).toBe("editing");
  });

  it("een nieuw, leeg bestand: meteen bewerken en opslaan als UTF-8 zonder BOM", async () => {
    const { s, saved } = session(new Uint8Array(0));
    await s.start();
    s.change("boodschappen\nmelk\n");
    await s.save();
    expect(dec(await blobBytes(saved[0]))).toBe("boodschappen\nmelk\n");
  });

  it("tijdens het opslaan: saving, en wat je intussen typt, blijft (onopgeslagen) in het veld", async () => {
    let finish: (n: FileNode) => void = () => {};
    const { s, saved } = session(enc("a"), (body) => {
      saved.push(body);
      return new Promise<FileNode>((resolve) => (finish = resolve));
    });
    await s.start();
    s.change("ab");
    const pending = s.save();
    expect(s.getState().phase).toBe("saving");
    expect(s.dirty()).toBe(true);
    // Doortypen na Ctrl+S, terwijl de server nog bezig is (review txt, punt 2)
    s.change("abc");
    expect(s.getState().text).toBe("abc");
    finish(fileNode({ id: "f1" }));
    await pending;
    // Opgeslagen is wat er bij Ctrl+S stond; de "c" staat er nog, en is nog niet opgeslagen
    expect(s.getState()).toMatchObject({ phase: "editing", text: "abc", saved: "ab" });
    expect(dec(await blobBytes(saved[0]))).toBe("ab");
    expect(s.dirty()).toBe(true);
    // Nog eens opslaan bewaart ook de rest
    const again = s.save();
    finish(fileNode({ id: "f1" }));
    await again;
    expect(dec(await blobBytes(saved[1]))).toBe("abc");
    expect(s.dirty()).toBe(false);
  });

  it("opslaan mislukt: de melding, en de tekst blijft (nog steeds onopgeslagen)", async () => {
    const { s } = session(enc("a"), async () => {
      throw new ApiError(413, "Onvoldoende quotum", "no_room");
    });
    await s.start();
    s.change("abc");
    expect(await s.save()).toBeNull();
    expect(s.getState()).toMatchObject({ phase: "editing", text: "abc", saved: "a", error: "Onvoldoende quotum" });
    expect(s.dirty()).toBe(true);
  });

  it("laden mislukt, een andere codering, of te groot: blijft lezen, met de reden", async () => {
    const failing = new TextEditSession({
      load: async () => {
        throw new Error("weg");
      },
      save: vi.fn(),
      errorText: (e) => (e as Error).message,
    });
    expect(await failing.start()).toBe(false);
    expect(failing.getState()).toMatchObject({ phase: "reading", error: "weg" });

    const latin = session(new Uint8Array([0x63, 0x61, 0x66, 0xe9])).s;
    expect(await latin.start()).toBe(false);
    expect(latin.getState()).toMatchObject({ phase: "reading", problem: "encoding" });

    const big = session(new Uint8Array(MAX_EDIT_BYTES + 1)).s;
    expect(await big.start()).toBe(false);
    expect(big.getState()).toMatchObject({ phase: "reading", problem: "too_large" });
  });

  it("stoppen: terug naar lezen, niets meer onopgeslagen", async () => {
    const { s } = session(enc("a"));
    await s.start();
    s.change("b");
    s.stop();
    expect(s.getState().phase).toBe("reading");
    expect(s.dirty()).toBe(false);
    s.change("c"); // lezen: geen tekst meer te veranderen
    expect(s.getState().text).toBe("");
  });
});

describe("weggaan met wijzigingen (de viewer)", () => {
  it("zonder wijzigingen meteen, met wijzigingen pas na ja", async () => {
    const { s } = session(enc("a"));
    const remove = setLeaveGuard(() => s.dirty());
    try {
      const run = vi.fn();
      const ask = vi.fn(async () => true);
      whenMayLeave(run, ask);
      expect(run).toHaveBeenCalledTimes(1); // synchroon: geen wijzigingen
      expect(ask).not.toHaveBeenCalled();

      await s.start();
      s.change("ab");
      expect(hasUnsaved()).toBe(true);
      const no = vi.fn(async () => false);
      whenMayLeave(run, no);
      await vi.waitFor(() => expect(no).toHaveBeenCalledTimes(1));
      expect(run).toHaveBeenCalledTimes(1);
      whenMayLeave(run, ask);
      await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(2));

      // De pagina verlaten: de browser vraagt het
      const e = { preventDefault: vi.fn() };
      onBeforeUnload(e);
      expect(e.preventDefault).toHaveBeenCalledTimes(1);
      await s.save();
      onBeforeUnload(e);
      expect(e.preventDefault).toHaveBeenCalledTimes(1);
    } finally {
      remove();
    }
    expect(hasUnsaved()).toBe(false);
  });

  it("een oude bewaker weghalen raakt een nieuwere niet", () => {
    const first = setLeaveGuard(() => true);
    const second = setLeaveGuard(() => true);
    first();
    expect(hasUnsaved()).toBe(true);
    second();
    expect(hasUnsaved()).toBe(false);
  });
});

describe("buiten de editor veranderd (review txt, punt 1)", () => {
  function tracked(content: Uint8Array) {
    const calls: { text: string; ifMatch: string | undefined }[] = [];
    let n = 0;
    let fail: ApiError | null = null;
    const { s } = session(content, async (body, ifMatch) => {
      calls.push({ text: dec(await blobBytes(body)), ifMatch });
      if (fail) {
        const e = fail;
        fail = null;
        throw e;
      }
      return fileNode({ id: "f1", name: "notities.txt", etag: `"na-${++n}"` });
    });
    return { s, calls, failNext: (e: ApiError) => (fail = e) };
  }

  it("opslaan stuurt de etag waar de tekst op voortbouwt (If-Match), daarna die van het opslaan", async () => {
    const { s, calls } = tracked(enc("a"));
    await s.start('"begin"');
    s.change("ab");
    await s.save();
    s.change("abc");
    await s.save();
    expect(calls).toEqual([
      { text: "ab", ifMatch: '"begin"' },
      { text: "abc", ifMatch: '"na-1"' },
    ]);
  });

  it("een andere etag terwijl je bewerkt: de tekst blijft, met de melding", async () => {
    const { s } = tracked(enc("a"));
    await s.start('"begin"');
    s.change("mijn tekst");
    s.seen('"begin"');
    expect(s.getState().changedOutside).toBe(false);
    s.seen('"van-een-ander"');
    expect(s.getState()).toMatchObject({ phase: "editing", text: "mijn tekst", changedOutside: true });
    expect(s.dirty()).toBe(true);
  });

  it("de etag van het eigen opslaan (de lijst kan eerder zijn dan het antwoord) is geen melding", async () => {
    let finish: (n: FileNode) => void = () => {};
    const { s } = session(enc("a"), () => new Promise<FileNode>((resolve) => (finish = resolve)));
    await s.start('"begin"');
    s.change("ab");
    const pending = s.save();
    s.seen('"eigen"'); // node.changed van dit opslaan, vóór het antwoord
    expect(s.getState().changedOutside).toBe(false);
    finish(fileNode({ id: "f1", etag: '"eigen"' }));
    await pending;
    expect(s.getState()).toMatchObject({ phase: "editing", changedOutside: false, saved: "ab" });
  });

  it("412 content_changed: niets weg, de melding; Toch opslaan zonder If-Match", async () => {
    const { s, calls, failNext } = tracked(enc("a"));
    await s.start('"begin"');
    s.change("mijn tekst");
    failNext(new ApiError(412, "Het bestand is intussen veranderd", "content_changed"));
    expect(await s.save()).toBeNull();
    expect(s.getState()).toMatchObject({ phase: "editing", text: "mijn tekst", saved: "a", changedOutside: true });
    const done = await s.overwrite();
    expect(done?.node.etag).toBe('"na-1"');
    expect(calls.map((c) => c.ifMatch)).toEqual(['"begin"', undefined]);
    expect(s.getState()).toMatchObject({ changedOutside: false, saved: "mijn tekst" });
  });

  it("Opnieuw laden: de inhoud van nu, met de etag van nu", async () => {
    let content = enc("oud");
    const loads = vi.fn(async () => content);
    const s = new TextEditSession({
      load: loads,
      save: vi.fn(async (_b: Blob, ifMatch?: string) => fileNode({ id: "f1", etag: `${ifMatch}+` })),
      errorText: String,
    });
    await s.start('"1"');
    s.change("mijn");
    s.seen('"2"');
    content = enc("nieuw");
    expect(await s.reload('"2"')).toBe(true);
    expect(s.getState()).toMatchObject({ phase: "editing", text: "nieuw", saved: "nieuw", changedOutside: false });
    s.change("nieuw!");
    const done = await s.save();
    expect(done?.node.etag).toBe('"2"+');
  });
});

describe("Terug in de browser (review txt, punt 4)", () => {
  function pop(idx: number | null, trusted = true) {
    return { isTrusted: trusted, state: idx === null ? null : { key: "k", idx }, stopImmediatePropagation: vi.fn() };
  }

  it("zonder wijzigingen gaat Terug gewoon door", () => {
    const remove = setLeaveGuard(() => false);
    try {
      const go = vi.fn();
      const handler = backGuard({ index: routerIndex, go, ask: vi.fn(async () => true) }, 3);
      const e = pop(2);
      handler(e);
      expect(e.stopImmediatePropagation).not.toHaveBeenCalled();
      expect(go).not.toHaveBeenCalled();
    } finally {
      remove();
    }
  });

  it("met wijzigingen: de stap terugdraaien en vragen; nee blijft, ja gaat alsnog terug", async () => {
    let dirty = true;
    const remove = setLeaveGuard(() => dirty);
    try {
      const go = vi.fn();
      let answer = false;
      const ask = vi.fn(async () => answer);
      const handler = backGuard({ index: routerIndex, go, ask }, 3);

      // Terug: de app ziet het niet, de stap wordt teruggedraaid, de vraag komt
      const back = pop(2);
      handler(back);
      expect(back.stopImmediatePropagation).toHaveBeenCalled();
      expect(go).toHaveBeenLastCalledWith(1);
      // Het terugdraaien zelf is ook een echte popstate: geen nieuwe vraag
      const restored = pop(3);
      handler(restored);
      expect(restored.stopImmediatePropagation).not.toHaveBeenCalled();
      await vi.waitFor(() => expect(ask).toHaveBeenCalledTimes(1));
      await Promise.resolve();
      expect(go).toHaveBeenCalledTimes(1); // nee: blijven

      // Nog eens, nu ja: dezelfde stap alsnog, zonder nieuwe vraag
      answer = true;
      handler(pop(2));
      handler(pop(3));
      await vi.waitFor(() => expect(go).toHaveBeenLastCalledWith(-1));
      const leave = pop(2);
      handler(leave);
      expect(leave.stopImmediatePropagation).not.toHaveBeenCalled();
      expect(ask).toHaveBeenCalledTimes(2); // ja gezegd: ook de stap zelf vraagt niet nog eens
      dirty = false;
    } finally {
      remove();
    }
  });

  it("navigate() van de app zelf (geen echte popstate) houdt hij niet tegen", () => {
    const remove = setLeaveGuard(() => true);
    try {
      const go = vi.fn();
      const handler = backGuard({ index: routerIndex, go, ask: vi.fn(async () => true) }, 3);
      const e = pop(3, false);
      handler(e);
      expect(e.stopImmediatePropagation).not.toHaveBeenCalled();
      expect(go).not.toHaveBeenCalled();
    } finally {
      remove();
    }
  });

  it("Vooruit (of meer stappen) draait hij de andere kant op terug; zonder plek: één", () => {
    const remove = setLeaveGuard(() => true);
    try {
      const go = vi.fn();
      const handler = backGuard({ index: routerIndex, go, ask: () => new Promise<boolean>(() => {}) }, 3);
      handler(pop(4));
      expect(go).toHaveBeenLastCalledWith(-1);
      const other = backGuard({ index: routerIndex, go, ask: () => new Promise<boolean>(() => {}) }, null);
      other(pop(null));
      expect(go).toHaveBeenLastCalledWith(1);
    } finally {
      remove();
    }
  });

  it("ja op sluiten: de stap terug van het sluiten zelf vraagt niet nog eens", async () => {
    const remove = setLeaveGuard(() => true);
    try {
      const run = vi.fn();
      whenMayLeave(run, async () => true);
      await vi.waitFor(() => expect(run).toHaveBeenCalled());
      const go = vi.fn();
      const handler = backGuard({ index: routerIndex, go, ask: vi.fn(async () => true) }, 3);
      const e = pop(2);
      handler(e);
      expect(e.stopImmediatePropagation).not.toHaveBeenCalled();
      // Een nieuwe bewaker (een volgend bestand) vraagt weer
      const again = setLeaveGuard(() => true);
      const e2 = pop(2);
      handler(e2);
      expect(e2.stopImmediatePropagation).toHaveBeenCalled();
      again();
    } finally {
      remove();
    }
  });
});

describe("opslaan over de lijn", () => {
  it("PUT op het bestand zelf, met If-Match als er een etag is", async () => {
    const seen: { path: string; init?: RequestInit }[] = [];
    const api = {
      rawRequest: vi.fn(async (path: string, init?: RequestInit) => {
        seen.push({ path, init });
        return new Response(JSON.stringify({ id: "n1", etag: '"nieuw"' }), { status: 200 });
      }),
    };
    const body = new Blob(["tekst"]);
    expect((await putContent(api, "n1", body, '"oud"')).etag).toBe('"nieuw"');
    await putContent(api, "n1", body);
    expect(seen[0].path).toBe("/api/v1/files/n1/content");
    expect(seen[0].init?.method).toBe("PUT");
    expect(seen[0].init?.body).toBe(body);
    expect(new Headers(seen[0].init?.headers).get("If-Match")).toBe('"oud"');
    expect(new Headers(seen[1].init?.headers).get("If-Match")).toBeNull();
  });
});
