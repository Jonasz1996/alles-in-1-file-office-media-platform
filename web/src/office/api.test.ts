import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import {
  checkpointAll,
  convert,
  deleteUnsaved,
  getAdmin,
  getFile,
  getStatus,
  newDocument,
  openSession,
  pdfLink,
  putAdmin,
  renewGate,
  restoreUnsaved,
  retryConversions,
  STATUS_RETRY,
  STATUS_TTL,
  StatusStore,
  saveNow,
} from "./api";
import { editorPath } from "./index";
import type { OfficeStatus } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

const on: OfficeStatus = {
  enabled: true,
  editing: true,
  state: "ok",
  edit_formats: ["docx", "xlsx", "pptx"],
  new_kinds: ["docx", "xlsx", "pptx"],
  max_bytes: 104857600,
};

/** Een store met een eigen klok en een nep-aanroep. */
function store(answers: (() => Promise<OfficeStatus>)[], canFetch = () => true) {
  let clock = 1_000_000;
  const fetchStatus = vi.fn(() => {
    const next = answers.shift();
    if (!next) throw new Error("geen antwoord meer");
    return next();
  });
  const s = new StatusStore(fetchStatus, canFetch, () => clock);
  return {
    s,
    fetchStatus,
    tick(ms: number) {
      clock += ms;
    },
  };
}

describe("de aanroepen (§3.4)", () => {
  it("elke functie gebruikt de methode en het pad van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    await getStatus();
    await getFile("a/b");
    await openSession("f1", { type: "mobile", theme: "dark" });
    await openSession("f1");
    await saveNow("f1");
    await renewGate();
    await convert("f1");
    await pdfLink("f1");
    await newDocument({ parent_id: "map", kind: "xlsx" });
    await getAdmin();
    await putAdmin({ enabled: true, max_editors: 20, checkpoint_minutes: 5, version_minutes: 60 });
    await checkpointAll();
    await retryConversions();
    await restoreUnsaved("k/1");
    await deleteUnsaved("k/1");
    expect(request.mock.calls).toEqual([
      ["GET", "/api/v1/office/status"],
      ["GET", "/api/v1/office/files/a%2Fb"],
      ["POST", "/api/v1/office/files/f1/session", { type: "mobile", theme: "dark" }],
      ["POST", "/api/v1/office/files/f1/session", {}],
      ["POST", "/api/v1/office/files/f1/save"],
      ["POST", "/api/v1/office/gate"],
      ["POST", "/api/v1/office/files/f1/convert"],
      ["POST", "/api/v1/office/files/f1/pdf"],
      ["POST", "/api/v1/office/new", { parent_id: "map", kind: "xlsx" }],
      ["GET", "/api/v1/admin/office"],
      ["PUT", "/api/v1/admin/office", { enabled: true, max_editors: 20, checkpoint_minutes: 5, version_minutes: 60 }],
      ["POST", "/api/v1/admin/office/checkpoint"],
      ["POST", "/api/v1/admin/office/conversions/retry"],
      ["POST", "/api/v1/admin/office/unsaved/k%2F1/restore"],
      ["DELETE", "/api/v1/admin/office/unsaved/k%2F1"],
    ]);
  });

  it("het adres van de editor (Keuze 32)", () => {
    expect(editorPath("0b5e")).toBe("/bewerken/0b5e");
    expect(editorPath("a b/c")).toBe("/bewerken/a%20b%2Fc");
  });
});

describe("de status in het geheugen", () => {
  it("eerst onbekend (null), dan het antwoord; tot STATUS_TTL niet opnieuw", async () => {
    const { s, fetchStatus, tick } = store([async () => on, async () => ({ ...on, editing: false })]);
    expect(s.peek()).toBeNull();
    expect(s.stale()).toBe(true);
    expect(s.get()).toBeNull(); // vraagt op de achtergrond
    await vi.waitFor(() => expect(s.peek()).toEqual(on));
    tick(STATUS_TTL - 1);
    expect(s.get()).toEqual(on);
    expect(fetchStatus).toHaveBeenCalledTimes(1);
    tick(1);
    expect(s.stale()).toBe(true);
    await s.load();
    expect(s.peek()?.editing).toBe(false);
    expect(fetchStatus).toHaveBeenCalledTimes(2);
  });

  it("twee keer tegelijk vragen is één verzoek", async () => {
    let answer: (v: OfficeStatus) => void = () => {};
    const { s, fetchStatus } = store([() => new Promise<OfficeStatus>((r) => (answer = r))]);
    const a = s.load();
    const b = s.load();
    s.get();
    expect(fetchStatus).toHaveBeenCalledTimes(1);
    answer(on);
    expect(await a).toEqual(on);
    expect(await b).toEqual(on);
  });

  it("een fout (geen verbinding, een server zonder office): null, en na STATUS_RETRY opnieuw", async () => {
    const { s, fetchStatus, tick } = store([
      () => Promise.reject(new ApiError(404, "Niet gevonden", "not_found")),
      async () => on,
    ]);
    expect(await s.load()).toBeNull();
    expect(s.peek()).toBeNull();
    tick(STATUS_RETRY - 1);
    expect(s.stale()).toBe(false);
    tick(1);
    expect(await s.load()).toEqual(on);
    expect(fetchStatus).toHaveBeenCalledTimes(2);
  });

  it("een fout na een goed antwoord: de haken gaan weg (null)", async () => {
    const { s, tick } = store([async () => on, () => Promise.reject(new Error("weg"))]);
    await s.load();
    tick(STATUS_TTL);
    expect(await s.load()).toBeNull();
    expect(s.peek()).toBeNull();
  });

  it("zonder login vraagt hij niets", async () => {
    let loggedIn = false;
    const { s, fetchStatus } = store([async () => on], () => loggedIn);
    expect(await s.load()).toBeNull();
    s.get();
    expect(fetchStatus).not.toHaveBeenCalled();
    loggedIn = true;
    expect(await s.load()).toEqual(on);
  });

  it("luisteraars horen alleen een andere status; hetzelfde antwoord is hetzelfde object", async () => {
    const { s, tick } = store([async () => on, async () => ({ ...on }), async () => ({ ...on, state: "down" })]);
    const heard = vi.fn();
    const stop = s.subscribe(heard);
    await s.load();
    const first = s.peek();
    expect(heard).toHaveBeenCalledTimes(1);
    tick(STATUS_TTL);
    await s.load();
    expect(s.peek()).toBe(first);
    expect(heard).toHaveBeenCalledTimes(1);
    tick(STATUS_TTL);
    await s.load();
    expect(s.peek()?.state).toBe("down");
    expect(heard).toHaveBeenCalledTimes(2);
    stop();
    s.set(null);
    expect(heard).toHaveBeenCalledTimes(2);
    expect(s.peek()).toBeNull();
  });

  it("set() van buitenaf telt als antwoord: niet meteen opnieuw vragen", () => {
    const { s, fetchStatus } = store([]);
    s.set(on);
    expect(s.stale()).toBe(false);
    expect(s.get()).toEqual(on);
    expect(fetchStatus).not.toHaveBeenCalled();
  });
});
