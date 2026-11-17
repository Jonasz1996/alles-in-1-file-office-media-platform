import { describe, expect, it } from "vitest";
import { tabHref, tabKeys, tabOf, tabsFor } from "./Beheer";

describe("de tabbladen van /beheer", () => {
  it("zonder office (of als de server het niet zegt) de zes van fase 3, en Zoeken en foto's (fase 6)", () => {
    for (const status of [null, { state: "off" as const }]) {
      const tabs = tabsFor(status);
      expect(tabs.map((tab) => tab.id)).toEqual(["overview", "users", "storage", "jobs", "groups", "security", "search"]);
      // /beheer/office is dan een onbekend tabblad: het overzicht
      expect(tabOf("office", tabs).id).toBe("overview");
    }
  });

  it("met office ingesteld (ook uitgezet of onbereikbaar) als zevende tabblad Office", () => {
    for (const state of ["starting", "ok", "down", "misconfigured"] as const) {
      const tabs = tabsFor({ state });
      expect(tabs).toHaveLength(8);
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
    expect(withOffice.map((tab) => tab.id)).toEqual([
      "overview",
      "users",
      "storage",
      "jobs",
      "groups",
      "security",
      "office",
      "media",
      "search",
    ]);
    const noOffice = tabsFor(null, true);
    expect(noOffice.map((tab) => tab.id)).toEqual(["overview", "users", "storage", "jobs", "groups", "security", "media", "search"]);
    expect(tabOf("media", noOffice)).toEqual({ id: "media", path: "media", label: "admin.media.tab" });
    expect(tabHref("media")).toBe("/beheer/media");
  });

  it("Alt+1 … Alt+9 staan vast bij hun tabblad: Alt+7 is Office, Alt+8 is Media, Alt+9 Zoeken en foto's", () => {
    const keys = tabKeys();
    expect(keys.map((k) => k.combo)).toEqual(["alt+1", "alt+2", "alt+3", "alt+4", "alt+5", "alt+6", "alt+7", "alt+8", "alt+9"]);
    expect(keys[6].tab.id).toBe("office");
    expect(keys[7].tab.id).toBe("media");
    expect(keys[8].tab.id).toBe("search");
  });

  it("Zoeken en foto's (fase 6) altijd als laatste tabblad, op /beheer/zoeken; met en zonder office en media", () => {
    for (const [status, media] of [
      [null, false],
      [null, true],
      [{ state: "ok" as const }, false],
      [{ state: "ok" as const }, true],
    ] as const) {
      const tabs = tabsFor(status, media);
      expect(tabs[tabs.length - 1]).toEqual({ id: "search", path: "zoeken", label: "admin.search.tab" });
      expect(tabs.filter((tab) => tab.id === "search")).toHaveLength(1);
      expect(tabOf("zoeken", tabs).id).toBe("search");
    }
    expect(tabHref("zoeken")).toBe("/beheer/zoeken");
    // Alt+9 is altijd Zoeken en foto's: het tabblad is er altijd, ook zonder Office en Media
    const alt9 = tabKeys().find((k) => k.combo === "alt+9");
    expect(alt9?.tab).toEqual(tabsFor(null)[tabsFor(null).length - 1]);
  });
});
