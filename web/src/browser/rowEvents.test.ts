import { afterEach, describe, expect, it, vi } from "vitest";
import { focusRow, focusRowNow, mayMoveFocus } from "./rowEvents";

// Nep-elementen: alleen de identiteit en contains() tellen
const el = (name: string) => ({ name }) as unknown as Element;

describe("uitgestelde focus op een rij", () => {
  const body = el("body");
  const row = el("rij");
  const other = el("andere rij");
  const menuItem = el("menu-item");
  const list = { contains: (n: Node | null) => n === (row as unknown) || n === (other as unknown) };

  it("verzet de focus als hij nog op dezelfde plek, in de lijst of nergens staat", () => {
    expect(mayMoveFocus(row, row, list, body)).toBe(true);
    expect(mayMoveFocus(other, row, list, body)).toBe(true);
    expect(mayMoveFocus(body, row, list, body)).toBe(true);
    expect(mayMoveFocus(null, row, list, body)).toBe(true);
  });

  it("laat de focus in een menu of dialoog die intussen openging (End en meteen Shift+F10)", () => {
    expect(mayMoveFocus(menuItem, row, list, body)).toBe(false);
    expect(mayMoveFocus(menuItem, row, null, body)).toBe(false);
  });

  it("wat al buiten de lijst de focus had (een knop) en die houdt, mag hem doorgeven", () => {
    const button = el("knop");
    expect(mayMoveFocus(button, button, list, body)).toBe(true);
  });
});

describe("focusRow en focusRowNow (snel achter elkaar)", () => {
  // Een nep-lijst met rijen op data-index, een nep-document en frames die de test zelf laat lopen
  function world(indices: number[]) {
    const body = { name: "body" };
    const doc = { body, activeElement: body as unknown };
    const rows = new Map<number, { index: number; focus: () => void }>();
    for (const index of indices) {
      const row = {
        index,
        focus: () => {
          doc.activeElement = row;
        },
      };
      rows.set(index, row);
    }
    const list = {
      querySelector: (sel: string) => rows.get(Number(/data-index="(\d+)"/.exec(sel)?.[1])) ?? null,
      contains: (n: unknown) => [...rows.values()].includes(n as never),
    };
    const frames: (() => void)[] = [];
    vi.stubGlobal("document", doc);
    vi.stubGlobal("requestAnimationFrame", (f: () => void) => frames.push(f));
    const frame = () => frames.splice(0).forEach((f) => f());
    const focused = () => (doc.activeElement as { index?: number }).index;
    return { container: { current: list as unknown as HTMLElement }, rows, frame, focused };
  }

  afterEach(() => vi.unstubAllGlobals());

  function addRow(w: ReturnType<typeof world>, index: number, doc = document as { activeElement: unknown }) {
    const row = {
      index,
      focus: () => {
        doc.activeElement = row;
      },
    };
    w.rows.set(index, row);
  }

  it("een eerdere vraag die nog wacht, zet de focus niet terug (de tweede rij staat er nog niet)", () => {
    // Zoals Shift+PageUp twee keer snel: de eerste rij staat er, de tweede pas een frame later
    const w = world([10]);
    focusRow(w.container, 10);
    focusRow(w.container, 40);
    w.frame();
    expect(w.focused()).toBeUndefined();
    addRow(w, 40);
    w.frame();
    expect(w.focused()).toBe(40);
  });

  it("een eerdere vraag die opnieuw probeert, vervalt bij een nieuwere", () => {
    const w = world([20]);
    focusRow(w.container, 10);
    w.frame();
    focusRow(w.container, 20);
    w.frame();
    expect(w.focused()).toBe(20);
    addRow(w, 10);
    w.frame();
    w.frame();
    expect(w.focused()).toBe(20);
  });

  it("focusRowNow: meteen op een rij die er al staat, en een wachtende focusRow vervalt", () => {
    const w = world([10, 20, 30]);
    focusRow(w.container, 10);
    focusRowNow(w.container, 20);
    expect(w.focused()).toBe(20);
    w.frame();
    expect(w.focused()).toBe(20);
  });

  it("een nieuwere vraag voor een andere lijst (de map eromheen) wint ook: er is maar één focus", () => {
    const w = world([10, 20]);
    const other = { current: w.container.current };
    focusRow(w.container, 10);
    focusRowNow(other, 20);
    w.frame();
    expect(w.focused()).toBe(20);
  });

  it("focusRowNow op een rij die er nog niet staat: zodra hij er staat", () => {
    const w = world([10]);
    focusRowNow(w.container, 30);
    expect(w.focused()).toBeUndefined();
    const row = { index: 30, focus: () => {} };
    w.rows.set(30, row);
    let got: unknown = null;
    row.focus = () => {
      got = row;
    };
    w.frame();
    expect(got).toBe(row);
  });
});
