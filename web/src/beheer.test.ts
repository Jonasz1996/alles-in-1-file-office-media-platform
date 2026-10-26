import { describe, expect, it } from "vitest";
import { tabHref, tabOf, tabsFor } from "./Beheer";

describe("de tabbladen van /beheer", () => {
  it("zonder office (of als de server het niet zegt) de zes van fase 3", () => {
    for (const status of [null, { state: "off" as const }]) {
      const tabs = tabsFor(status);
      expect(tabs.map((tab) => tab.id)).toEqual(["overview", "users", "storage", "jobs", "groups", "security"]);
      // /beheer/office is dan een onbekend tabblad: het overzicht
      expect(tabOf("office", tabs).id).toBe("overview");
    }
  });

  it("met office ingesteld (ook uitgezet of onbereikbaar) als zevende tabblad Office", () => {
    for (const state of ["starting", "ok", "down", "misconfigured"] as const) {
      const tabs = tabsFor({ state });
      expect(tabs).toHaveLength(7);
      expect(tabs[6]).toEqual({ id: "office", path: "office", label: "admin.office.tab" });
      expect(tabOf("office", tabs).id).toBe("office");
    }
    expect(tabHref("office")).toBe("/beheer/office");
    expect(tabOf(undefined).id).toBe("overview");
  });
});
