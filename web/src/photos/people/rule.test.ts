import { describe, expect, it } from "vitest";
import { EMPTY_RULE_FORM, formOf, ruleEmpty, ruleOf, sameRule, shareAnswer, sharedCount } from "./rule";
import type { AlbumRuleInfo } from "./types";

function info(over: Partial<AlbumRuleInfo> = {}): AlbumRuleInfo {
  return {
    rule: {},
    share_new: false,
    refreshed_at: "2024-06-01T12:00:00Z",
    count: 0,
    code: null,
    items: 0,
    private: 0,
    ...over,
  };
}

describe("formOf", () => {
  it("zonder regel: een leeg formulier", () => {
    expect(formOf(null)).toEqual(EMPTY_RULE_FORM);
    expect(formOf(undefined)).toEqual(EMPTY_RULE_FORM);
  });

  it("neemt naam of tekst, personen, soort, periode en share_new over", () => {
    const f = formOf(
      info({ rule: { q: "strand", person: ["p1"], type: ["video"], from: "2024-01-01", to: "2024-12-31" }, share_new: true }),
    );
    expect(f).toEqual({
      q: "strand",
      person: ["p1"],
      type: "video",
      from: "2024-01-01",
      to: "2024-12-31",
      shareNew: true,
    });
  });

  it("een soort die de web niet kent, of meer soorten, telt niet", () => {
    expect(formOf(info({ rule: { type: ["pdf"] } })).type).toBeNull();
    expect(formOf(info({ rule: { type: ["image", "video"] } })).type).toBeNull();
  });
});

describe("ruleOf", () => {
  it("zonder wat leeg is", () => {
    expect(ruleOf(EMPTY_RULE_FORM)).toEqual({});
    expect(ruleOf({ ...EMPTY_RULE_FORM, q: "   " })).toEqual({});
  });

  it("naam of tekst zonder dubbele spaties, personen, soort als lijst", () => {
    expect(ruleOf({ ...EMPTY_RULE_FORM, q: "  oma   ria ", person: ["p1", "p2"], type: "image" })).toEqual({
      q: "oma ria",
      person: ["p1", "p2"],
      type: ["image"],
    });
  });

  it("een omgekeerde periode wordt omgedraaid", () => {
    expect(ruleOf({ ...EMPTY_RULE_FORM, from: "2024-12-31", to: "2024-01-01" })).toEqual({
      from: "2024-01-01",
      to: "2024-12-31",
    });
  });

  it("wat de web niet laat zien, blijft uit de regel die er stond", () => {
    const base = { q: "oud", country: "NL", tag: ["t1"], person: ["oud"] };
    expect(ruleOf({ ...EMPTY_RULE_FORM, q: "nieuw" }, base)).toEqual({ country: "NL", tag: ["t1"], q: "nieuw" });
  });
});

describe("ruleEmpty", () => {
  it("een regel zonder naam of tekst en zonder filter zoekt niets", () => {
    expect(ruleEmpty({})).toBe(true);
    expect(ruleEmpty({ q: "", person: [] })).toBe(true);
    expect(ruleEmpty({ person: ["p1"] })).toBe(false);
    expect(ruleEmpty({ from: "2024-01-01" })).toBe(false);
  });
});

describe("sharedCount", () => {
  it("hoeveel items uit de regel gedeeld worden, nooit minder dan nul", () => {
    expect(sharedCount({ items: 10, private: 3 })).toBe(7);
    expect(sharedCount({ items: 2, private: 5 })).toBe(0);
  });
});

describe("shareAnswer (review golf 2, veilig V1)", () => {
  const ja = { ...EMPTY_RULE_FORM, q: "strand", shareNew: true };

  it("dezelfde regel: het vinkje zoals het staat", () => {
    expect(shareAnswer(ja, { q: "strand" }, { q: "strand" }, false)).toBe(true);
    expect(shareAnswer({ ...ja, shareNew: false }, { q: "strand" }, { q: "strand" }, false)).toBe(false);
  });

  it("een andere regel zonder dat de eigenaar het vinkje aanraakte: niets, dan vraagt de API opnieuw", () => {
    expect(shareAnswer(ja, { q: "kinderen" }, { q: "strand" }, false)).toBeUndefined();
    expect(shareAnswer(ja, { q: "strand", from: "2024-01-01" }, { q: "strand" }, false)).toBeUndefined();
  });

  it("een andere regel en het vinkje zelf gekozen: dat antwoord", () => {
    expect(shareAnswer(ja, { q: "kinderen" }, { q: "strand" }, true)).toBe(true);
    expect(shareAnswer({ ...ja, shareNew: false }, { q: "kinderen" }, { q: "strand" }, true)).toBe(false);
  });

  it("sameRule kijkt niet naar de volgorde van de velden, wel naar die van een lijst", () => {
    expect(sameRule({ q: "a", from: "2024-01-01" }, { from: "2024-01-01", q: "a" })).toBe(true);
    expect(sameRule({ person: ["p1", "p2"] }, { person: ["p2", "p1"] })).toBe(false);
    expect(sameRule({ q: "a" }, { q: "a", tag: undefined })).toBe(true);
  });
});
