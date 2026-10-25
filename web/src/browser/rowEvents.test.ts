import { describe, expect, it } from "vitest";
import { mayMoveFocus } from "./rowEvents";

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
