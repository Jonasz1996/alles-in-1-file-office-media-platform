import { beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { loadAreas, resetI18nForTests } from "../i18n";
import type { ToastOptions } from "../ui/toast";
import { handle, type NoticeDeps, noticeFor } from "./notices";

beforeAll(async () => {
  resetI18nForTests("nl");
  await loadAreas(["common", "office"]);
});

function deps(node?: NoticeDeps["node"]) {
  const shown: { message: string; o: ToastOptions }[] = [];
  const d: NoticeDeps & { shown: typeof shown } = {
    shown,
    show: (message, o) => shown.push({ message, o }),
    node: node ?? (async (id) => ({ id, kind: "file" as const, parent_id: "map1" })),
    go: vi.fn(),
    failed: vi.fn(),
  };
  return d;
}

describe("office.unsaved", () => {
  it.each([
    ["no_room", "er is geen ruimte meer"],
    ["too_large", "te groot"],
    ["owner_gone", "de eigenaar bestaat niet meer"],
    ["error_status", "de editor meldde een fout"],
  ])("final true (%s): een melding die blijft staan, zonder actie", (reason, text) => {
    const d = deps();
    handle({ type: "office.unsaved", data: { node_id: "n1", name: "verslag.docx", reason, final: true } }, d);
    expect(d.shown).toHaveLength(1);
    expect(d.shown[0].message).toContain("verslag.docx");
    expect(d.shown[0].message).toContain(text);
    expect(d.shown[0].o).toMatchObject({ kind: "error", timeoutMs: 0, action: undefined });
  });

  it("final false: niets hier (dat toont de editorpagina)", () => {
    const d = deps();
    handle({ type: "office.unsaved", data: { node_id: "n1", name: "x", reason: "no_room", final: false } }, d);
    expect(d.shown).toEqual([]);
  });

  it("een onbekende reden of een andere gebeurtenis: niets", () => {
    const d = deps();
    handle({ type: "office.unsaved", data: { node_id: "n1", name: "x", reason: "iets", final: true } }, d);
    handle({ type: "office.presence", data: { node_id: "n1", count: 2 } }, d);
    handle({ type: "office.dropped", data: { node_id: "n1", key: "k", reason: "access" } }, d);
    expect(d.shown).toEqual([]);
    expect(noticeFor({ type: "node.changed", data: { id: "n1" } })).toBeNull();
  });
});

describe("office.recovered", () => {
  it.each([
    ["status3", "herstelkopie"],
    ["no_writer", "Niemand mocht het origineel nog bewerken"],
    ["forgotten", "is teruggezet"],
  ])("%s: een melding met Openen, die naar de kopie gaat", async (reason, text) => {
    const d = deps();
    handle(
      {
        type: "office.recovered",
        data: { node_id: "n1", copy_id: "c1", name: "verslag (hersteld 2026-10-27 14.05).docx", reason },
      },
      d,
    );
    expect(d.shown).toHaveLength(1);
    const { message, o } = d.shown[0];
    expect(message).toContain(text);
    expect(message).toContain("verslag (hersteld 2026-10-27 14.05).docx");
    expect(o).toMatchObject({ kind: "info", timeoutMs: 0, id: "office-office.recovered-c1" });
    expect(o.action?.label).toBe("Openen");
    o.action?.run();
    await vi.waitFor(() => expect(d.go).toHaveBeenCalledWith("/map/map1?bestand=c1"));
  });

  it("de kopie is niet meer te vinden: een foutmelding", async () => {
    const d = deps(async () => {
      throw new ApiError(404, "Niet gevonden", "not_found");
    });
    handle({ type: "office.recovered", data: { node_id: null, copy_id: "c1", name: "x", reason: "forgotten" } }, d);
    d.shown[0].o.action?.run();
    await vi.waitFor(() => expect(d.failed).toHaveBeenCalledWith("Niet gevonden"));
    expect(d.go).not.toHaveBeenCalled();
  });

  it("dezelfde kopie twee keer: dezelfde melding (hetzelfde id)", () => {
    const d = deps();
    const event = { type: "office.recovered", data: { node_id: "n1", copy_id: "c1", name: "x", reason: "status3" } };
    handle(event, d);
    handle(event, d);
    expect(d.shown.map((s) => s.o.id)).toEqual(["office-office.recovered-c1", "office-office.recovered-c1"]);
  });
});
