import { describe, expect, it } from "vitest";
import { canEdit } from "./actions";
import { editorPath } from "./index";
import type { OfficeStatus, OfficeTarget } from "./types";

const on: OfficeStatus = {
  enabled: true,
  editing: true,
  state: "ok",
  edit_formats: ["docx", "xlsx", "pptx"],
  new_kinds: ["docx", "xlsx", "pptx"],
  max_bytes: 100 * 1024 * 1024,
};

function file(extra: Partial<OfficeTarget> = {}): OfficeTarget {
  return { id: "f1", name: "Verslag.docx", mime: null, size: 1000, kind: "file", ...extra };
}

describe("wanneer Bewerken er is (Keuze 22, 23 en 32)", () => {
  it("office aan, een docx, xlsx of pptx met schrijfrecht: zichtbaar en het kan", () => {
    for (const name of ["Verslag.docx", "LIJST.XLSX", "a.b.pptx"]) {
      expect(canEdit(file({ name }), on)).toEqual({ visible: true, reason: null });
    }
    // Zonder permission is het van jou; write via een deling ook
    expect(canEdit(file({ permission: "write" }), on)).toEqual({ visible: true, reason: null });
  });

  it.each([
    ["geen status (office uit of onbekend)", file(), null],
    ["office ingesteld maar uitgezet", file(), { ...on, enabled: false, editing: false }],
    ["niet ingesteld", file(), { ...on, enabled: false, editing: false, state: "off" as const }],
    ["een map", file({ kind: "dir", name: "Map.docx" }), on],
    ["een album", file({ kind: "album" }), on],
    ["een eerdere versie", file({ version: { id: "v1" } }), on],
    ["een ander formaat", file({ name: "oud.doc" }), on],
    ["odt (alleen bekijken)", file({ name: "tekst.odt" }), on],
    ["zonder extensie", file({ name: "docx" }), on],
    ["alleen lezen", file({ permission: "read" }), on],
  ])("verborgen: %s", (_label, target, status) => {
    expect(canEdit(target, status)).toEqual({ visible: false, reason: null });
  });

  it("een formaat dat de server niet bewerkt, ook niet", () => {
    expect(canEdit(file({ name: "lijst.xlsx" }), { ...on, edit_formats: ["docx"] }).visible).toBe(false);
  });

  it.each([
    ["in de prullenbak", file({ deleted_at: "2026-10-27T09:00:00Z" }), on, "trashed"],
    ["te groot", file({ size: on.max_bytes + 1 }), on, "too_large"],
    ["DS weg (editing false)", file(), { ...on, editing: false, state: "down" as const }, "office_down"],
    ["DS verkeerd ingesteld", file(), { ...on, editing: false, state: "misconfigured" as const }, "office_down"],
  ])("zichtbaar maar nu niet: %s", (_label, target, status, reason) => {
    expect(canEdit(target, status)).toEqual({ visible: true, reason });
  });

  it("precies max_bytes mag nog", () => {
    expect(canEdit(file({ size: on.max_bytes }), on)).toEqual({ visible: true, reason: null });
  });

  it("de editor heeft een eigen adres", () => {
    expect(editorPath("a b/c")).toBe("/bewerken/a%20b%2Fc");
  });
});
