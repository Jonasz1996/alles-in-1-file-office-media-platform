import { describe, expect, it } from "vitest";
import { ICON_NAMES, SHELL_PATHS } from "./Icon";
import { PATHS } from "./iconPaths";

describe("iconen", () => {
  it("elk icoon heeft paden, in de schil of in de eigen chunk, en nooit in allebei", () => {
    for (const name of ICON_NAMES) {
      const inShell = name in SHELL_PATHS;
      const inChunk = name in PATHS;
      expect(inShell !== inChunk, name).toBe(true);
      expect((SHELL_PATHS[name] ?? PATHS[name])?.length, name).toBeGreaterThan(0);
    }
    expect(Object.keys(SHELL_PATHS).length + Object.keys(PATHS).length).toBe(ICON_NAMES.length);
  });

  it("de schil heeft wat de balk, het accountmenu, dialogen en meldingen gebruiken", () => {
    for (const name of ["user", "logout", "sun", "moon", "language", "keyboard", "check", "chevron-right", "close"]) {
      expect(SHELL_PATHS, name).toHaveProperty(name);
    }
  });
});
