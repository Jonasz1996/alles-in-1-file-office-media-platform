import { describe, expect, it } from "vitest";
import { MAX_SUGGESTIONS, nameMatches, suggest } from "./combo";
import { filterPeople } from "./PeoplePage";
import type { Person } from "./types";

function person(id: string, name: string, extra: Partial<Person> = {}): Person {
  return { id, name, count: 1, cover: null, hidden: false, ...extra };
}

const mensen = [person("1", "Oma Ria"), person("2", "Ánna de Vries"), person("3", "Bram"), person("4", "Jan-Willem")];

describe("nameMatches", () => {
  it("een woord van de naam begint met wat je typt, zonder accenten en hoofdletters", () => {
    expect(nameMatches("Oma Ria", "ria")).toBe(true);
    expect(nameMatches("Ánna de Vries", "anna")).toBe(true);
    expect(nameMatches("Ánna de Vries", "vri")).toBe(true);
    expect(nameMatches("Jan-Willem", "wil")).toBe(true);
    expect(nameMatches("Bram", "ram")).toBe(false);
  });

  it("niets getypt past bij iedereen", () => {
    expect(nameMatches("Bram", "")).toBe(true);
  });
});

describe("suggest", () => {
  it("wie zo begint eerst, dan op naam", () => {
    const list = [person("a", "Bart"), person("b", "Ab Bakker"), person("c", "Bas")];
    expect(suggest(list, "ba").map((o) => (o.kind === "person" ? o.person.name : o.name))).toEqual([
      "Bart",
      "Bas",
      "Ab Bakker",
    ]);
  });

  it("laat weg wie er al staat", () => {
    const got = suggest(mensen, "", { exclude: new Set(["1", "3"]) });
    expect(got.map((o) => (o.kind === "person" ? o.person.id : "nieuw"))).toEqual(["2", "4"]);
  });

  it("met allowNew een nieuwe naam als niemand precies zo heet", () => {
    const got = suggest(mensen, "  Oma   R ", { allowNew: true });
    expect(got).toEqual([{ kind: "person", person: mensen[0] }, { kind: "new", name: "Oma R" }]);
  });

  it("geen nieuwe naam als iemand zo heet (ook met andere hoofdletters of accenten)", () => {
    const got = suggest(mensen, "anna de vries", { allowNew: true });
    expect(got.some((o) => o.kind === "new")).toBe(false);
  });

  it("geen nieuwe naam zonder allowNew of zonder tekst", () => {
    expect(suggest(mensen, "Zoë").length).toBe(0);
    expect(suggest(mensen, "   ", { allowNew: true }).some((o) => o.kind === "new")).toBe(false);
  });

  it("hooguit MAX_SUGGESTIONS personen", () => {
    const many = Array.from({ length: 20 }, (_, i) => person(String(i), `Kees ${i}`));
    expect(suggest(many, "kees").length).toBe(MAX_SUGGESTIONS);
  });
});

describe("filterPeople (/fotos/personen)", () => {
  it("op naam gesorteerd, alleen wie past", () => {
    expect(filterPeople(mensen, "").map((p) => p.id)).toEqual(["2", "3", "4", "1"]);
    expect(filterPeople(mensen, " RIA ").map((p) => p.id)).toEqual(["1"]);
    expect(filterPeople(mensen, "x")).toEqual([]);
  });
});
