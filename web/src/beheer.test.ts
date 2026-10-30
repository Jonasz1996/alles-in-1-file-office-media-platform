import { describe, expect, it } from "vitest";
import { tabHref, tabKeys, tabOf, tabsFor } from "./Beheer";

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

  it("media (fase 5) als achtste tabblad, alleen als /admin/media antwoordt; met en zonder office", () => {
    const without = tabsFor({ state: "ok" }, false);
    expect(without.map((tab) => tab.id)).not.toContain("media");
    expect(tabOf("media", without).id).toBe("overview");
    const withOffice = tabsFor({ state: "ok" }, true);
    expect(withOffice.map((tab) => tab.id)).toEqual(["overview", "users", "storage", "jobs", "groups", "security", "office", "media"]);
    const noOffice = tabsFor(null, true);
    expect(noOffice.map((tab) => tab.id)).toEqual(["overview", "users", "storage", "jobs", "groups", "security", "media"]);
    expect(tabOf("media", noOffice)).toEqual({ id: "media", path: "media", label: "admin.media.tab" });
    expect(tabHref("media")).toBe("/beheer/media");
  });

  it("Alt+1 … Alt+8 staan vast bij hun tabblad: Alt+7 is Office, Alt+8 is Media", () => {
    const keys = tabKeys();
    expect(keys.map((k) => k.combo)).toEqual(["alt+1", "alt+2", "alt+3", "alt+4", "alt+5", "alt+6", "alt+7", "alt+8"]);
    expect(keys[6].tab.id).toBe("office");
    expect(keys[7].tab.id).toBe("media");
  });
});
