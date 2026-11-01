// Het tabblad Media op /beheer (M10): wat MediaTab.tsx los rekent, en dat elke staat, reden en
// soort een tekst heeft in het Nederlands en het Engels (geen sleutel of {naam} in beeld).
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { loadAreas, type MessageKey, resetI18nForTests, t } from "../i18n";
import type { ImportView, MediaAdmin } from "../media/types";
import { PLAN_REASONS } from "../media/types";
import {
  CONFIG_LIMITS,
  importTarget,
  loadtestLines,
  loadtestOff,
  mergeImport,
  parseConfigNumber,
  reasonColumns,
} from "./MediaTab";

async function inLocale(locale: "nl" | "en") {
  resetI18nForTests(locale);
  await loadAreas(["admin", "common"]);
}

beforeAll(() => inLocale("nl"));
afterEach(() => inLocale("nl"));

describe("de grenzen van de instellingen", () => {
  it("zijn die van MediaConfig (app/config.py)", () => {
    expect(CONFIG_LIMITS.cpu_budget).toMatchObject({ min: 0.5, max: 16 });
    expect(CONFIG_LIMITS.max_user_transcodes).toMatchObject({ min: 1, max: 16 });
    expect(CONFIG_LIMITS.cache_gb).toMatchObject({ min: 1, max: 10_000 });
  });

  it("cpu_budget mag een komma hebben, binnen 0,5 tot 16", () => {
    expect(parseConfigNumber("cpu_budget", "2,5")).toBe(2.5);
    expect(parseConfigNumber("cpu_budget", " 16 ")).toBe(16);
    expect(parseConfigNumber("cpu_budget", "0.5")).toBe(0.5);
    expect(parseConfigNumber("cpu_budget", "0.4")).toBeUndefined();
    expect(parseConfigNumber("cpu_budget", "16.5")).toBeUndefined();
    expect(parseConfigNumber("cpu_budget", "")).toBeUndefined();
    expect(parseConfigNumber("cpu_budget", "-1")).toBeUndefined();
    expect(parseConfigNumber("cpu_budget", "1e1")).toBeUndefined();
  });

  it("de rest zijn hele getallen", () => {
    expect(parseConfigNumber("max_transcodes", "0")).toBe(0);
    expect(parseConfigNumber("max_transcodes", "3")).toBe(3);
    expect(parseConfigNumber("max_transcodes", "2.5")).toBeUndefined();
    expect(parseConfigNumber("max_transcodes", "17")).toBeUndefined();
    expect(parseConfigNumber("max_user_transcodes", "0")).toBeUndefined();
    expect(parseConfigNumber("cache_gb", "20")).toBe(20);
    expect(parseConfigNumber("cache_gb", "10001")).toBeUndefined();
  });
});

describe("de redenen van 7 dagen", () => {
  it("alleen de redenen die er waren, het vaakst eerst", () => {
    const days: MediaAdmin["reasons"] = [
      { day: "2026-10-31", counts: { video_codec: 2, container: 1 } },
      { day: "2026-11-01", counts: { video_codec: 1, hdr: 3, audio_codec: 0 } },
    ];
    expect(reasonColumns(days)).toEqual(["hdr", "video_codec", "container"]);
    expect(reasonColumns([])).toEqual([]);
  });

  it("elke reden van Keuze 19 heeft een tekst", async () => {
    for (const locale of ["nl", "en"] as const) {
      await inLocale(locale);
      for (const reason of PLAN_REASONS) {
        const text = t(`admin.media.reasons.names.${reason}` as MessageKey);
        expect(text, `${locale} ${reason}`).not.toMatch(/admin\.|[{}]/);
      }
    }
  });
});

const VIEW: ImportView = {
  job: "j1",
  state: "running",
  source: "Films",
  target_id: "t",
  owner_id: "o",
  move: false,
  done: 1,
  total: 10,
  skipped: 1,
  bytes: 100,
  skipped_items: [{ path: "Films/kapot.mkv", reason: "in gebruik" }],
};

describe("de import live (event media.import)", () => {
  it("dezelfde job: de tellers uit het event, de lijst blijft", () => {
    const next = mergeImport(VIEW, { job: "j1", state: "done", done: 10, total: 10, skipped: 1, bytes: 900 });
    expect(next).toMatchObject({ state: "done", done: 10, bytes: 900, source: "Films" });
    expect(next?.skipped_items).toEqual(VIEW.skipped_items);
  });

  it("een andere job vervangt alles; onzin verandert niets", () => {
    const next = mergeImport(VIEW, { ...VIEW, job: "j2", skipped_items: undefined, state: "queued", done: 0 });
    expect(next).toMatchObject({ job: "j2", state: "queued", done: 0, skipped_items: [] });
    expect(mergeImport(VIEW, { state: "done" })).toBe(VIEW);
    expect(mergeImport(null, { job: 3 })).toBeNull();
    expect(mergeImport(null, { ...VIEW, skipped_items: undefined })?.skipped_items).toEqual([]);
  });
});

describe("de doelmap van de import", () => {
  const me = { id: "ik", root_id: "mijn-root" };
  const bert = { id: "bert", root_id: "bert-root" };

  it("een gekozen map van jezelf, anders de hoofdmap van de eigenaar", () => {
    expect(importTarget(me, me, null)).toBe("mijn-root");
    expect(importTarget(me, me, { id: "films" })).toBe("films");
    // een gekozen map telt niet voor een ander: die is van mij
    expect(importTarget(bert, me, { id: "films" })).toBe("bert-root");
    expect(importTarget(null, me, null)).toBeNull();
    expect(importTarget({ id: "x", root_id: "" }, me, null)).toBeNull();
  });
});

describe("de belastingtest", () => {
  it("uit op de server: 503 media_off met reason loadtest", () => {
    expect(loadtestOff({ code: "media_off", params: { reason: "loadtest" } })).toBe(true);
    expect(loadtestOff({ code: "media_off", params: {} })).toBe(false);
    expect(loadtestOff({ code: "media_bench_busy" })).toBe(false);
    expect(loadtestOff(null)).toBe(false);
  });

  const report = {
    phases: {
      a: { starts_s: [3.1, 3.4], stalls: 0, passed: true, problems: [] },
      b: { starts_s: [4.2], stalls: 2, passed: false, problems: ["2 keer haperen (hooguit 0)"] },
      c: { skipped: "alleen met de spelers buiten de app (--extern)" },
    },
    api_p95_ms: 41.5,
    passed: false,
    summary: ["(van de server)"],
  };

  it("het rapport per fase, met 0 keer haperen en de uitkomst", () => {
    expect(loadtestLines(null)).toEqual([]);
    expect(loadtestLines(report)).toEqual([
      "Fase a: 0 keer haperen; geslaagd",
      "Fase b: 2 keer haperen; niet geslaagd: 2 keer haperen (hooguit 0)",
      "Fase c: niet gedraaid (alleen met de spelers buiten de app (--extern))",
      "API p95: 41,5 ms",
      "Uitkomst: niet geslaagd",
    ]);
  });

  it("in het Engels, met een fout en een stop", async () => {
    await inLocale("en");
    const lines = loadtestLines({ error: "WorkerFailed", stopped: "4 ffmpeg-processen (hooguit 3)", passed: false });
    expect(lines).toEqual(["Stopped: 4 ffmpeg-processen (hooguit 3)", "WorkerFailed", "Result: not passed"]);
  });
});

describe("de teksten van het tabblad", () => {
  const ENUMS: Record<string, readonly string[]> = {
    "admin.media.state": ["starting", "ok", "down", "ffmpeg_missing"],
    "admin.media.worker.mode": ["container", "local"],
    "admin.media.worker.classes": ["transcode", "remux", "background", "short", "control"],
    "admin.media.sessions.modes": ["direct", "remux", "transcode", "audio"],
    "admin.media.libraries.kinds": ["movies", "shows", "music", "videos"],
    "admin.media.cache.parts": ["sessions", "subs", "trick", "art", "audio"],
    "admin.media.providers.names": ["tmdb", "musicbrainz", "caa"],
    "admin.media.providers.breakers": ["closed", "open", "half_open"],
    "admin.media.import.states": ["queued", "running", "done", "failed", "stopped"],
    "admin.media.loadtest.states": ["idle", "making_sources", "running", "done", "failed", "stopped"],
  };

  it("elke staat en soort heeft een tekst, in het Nederlands en het Engels", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      for (const locale of ["nl", "en"] as const) {
        await inLocale(locale);
        for (const [base, values] of Object.entries(ENUMS)) {
          for (const value of values) {
            const text = t(`${base}.${value}` as MessageKey);
            expect(text, `${locale} ${base}.${value}`).not.toMatch(/admin\.|[{}]/);
          }
        }
      }
      expect(errors).not.toHaveBeenCalled();
    } finally {
      errors.mockRestore();
    }
  });

  it("meervouden en namen worden ingevuld", async () => {
    expect(t("admin.media.jobs.retried", { n: 1 })).toBe("1 analyse staat opnieuw klaar.");
    expect(t("admin.media.jobs.retried", { n: 3 })).toBe("3 analyses staan opnieuw klaar.");
    expect(t("admin.media.libraries.inside", { name: "Films" })).toContain("Films");
    await inLocale("en");
    expect(t("admin.media.budget.nativeReloads", { n: 2 })).toBe("Native players had to reload 2 times since the start.");
  });
});
