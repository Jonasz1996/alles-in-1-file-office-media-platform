import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { fold, loadAreas, resetI18nForTests } from "../i18n";
import type { AdminUser } from "./api";
import {
  httpsErrors,
  httpsErrorText,
  jobSubject,
  jobTypeLabel,
  matchesUser,
  noticeText,
  parseQuota,
  quotaInput,
  segments,
} from "./format";
import { byTypeRows } from "./Jobs";
import { partLabel } from "./Storage";
import { changesOf } from "./UserDialog";

const GB = 1024 ** 3;

beforeAll(async () => {
  resetI18nForTests("nl");
  await loadAreas(["admin"]);
});

afterEach(async () => {
  resetI18nForTests("nl");
  await loadAreas(["admin"]);
});

describe("segments (de balk van een schijf)", () => {
  const none = { blobs: 0, previews: 0, uploads: 0, database: 0, other: 0 };

  it("in procenten van het totaal, lege stukken vallen weg", () => {
    const s = segments({ ...none, blobs: 50, database: 10 }, 100, 40);
    expect(s).toEqual([
      { key: "blobs", bytes: 50, percent: 50 },
      { key: "database", bytes: 10, percent: 10 },
      { key: "free", bytes: 40, percent: 40 },
    ]);
  });

  it("een heel klein stuk krijgt minstens 0,5 %, samen nooit meer dan 100", () => {
    const s = segments({ ...none, blobs: 999_000, previews: 1 }, 1_000_000, 999);
    const previews = s.find((x) => x.key === "previews");
    expect(previews?.percent).toBe(0.5);
    const sum = s.reduce((a, x) => a + x.percent, 0);
    expect(sum).toBeLessThanOrEqual(100 + 1e-9);
    expect(s.every((x) => x.percent >= 0.5)).toBe(true);
  });

  it("meer dan de schijf (meting loopt achter): afgekapt op 100", () => {
    const s = segments({ ...none, blobs: 80, other: 40 }, 100, 0);
    expect(s.reduce((a, x) => a + x.percent, 0)).toBeCloseTo(100, 5);
  });

  it("zonder totaal geen balk; negatieve getallen tellen als 0", () => {
    expect(segments({ ...none, blobs: 5 }, 0)).toEqual([]);
    expect(segments({ ...none, blobs: -5, other: 10 }, 10)).toEqual([{ key: "other", bytes: 10, percent: 100 }]);
  });

  it("de namen van de stukken", () => {
    expect(partLabel("other")).toBe("Overig");
    expect(partLabel("free")).toBe("Vrij");
  });
});

describe("noticeText (meldingen op code)", () => {
  it("bekende codes vertaald, met params", async () => {
    expect(noticeText({ code: "disk_full", params: { node: "n1", percent: 80 }, text: "x" })).toBe(
      "n1: datadisk meer dan 80 % vol.",
    );
    resetI18nForTests("en");
    await loadAreas(["common", "admin"]);
    expect(noticeText({ code: "jobs_waiting", params: { minutes: 20 }, text: "x" })).toBe(
      "The oldest background job has been waiting for 20 minutes.",
    );
    expect(
      noticeText({ code: "https_error", params: { error: "acme_dns", detail: "NXDOMAIN" }, text: "x" }),
    ).toBe("HTTPS: The domain does not point at your public IP address (yet). Check the DNS setting. (NXDOMAIN)");
  });

  it("het certificaat verloopt: enkelvoud, meervoud en vandaag", async () => {
    resetI18nForTests("nl");
    await loadAreas(["common", "admin"]);
    const expiring = (days: number) => noticeText({ code: "https_expiring", params: { days }, text: "x" });
    expect(expiring(1)).toBe("Het certificaat verloopt over 1 dag.");
    expect(expiring(13)).toBe("Het certificaat verloopt over 13 dagen.");
    expect(expiring(0)).toBe("Het certificaat verloopt vandaag.");
    resetI18nForTests("en");
    await loadAreas(["common", "admin"]);
    expect(expiring(1)).toBe("The certificate expires in 1 day.");
    expect(expiring(2)).toBe("The certificate expires in 2 days.");
    expect(expiring(0)).toBe("The certificate expires today.");
    resetI18nForTests("nl");
    await loadAreas(["common", "admin"]);
  });

  it("een onbekende code houdt de tekst van de server", () => {
    expect(noticeText({ code: "iets_nieuws", params: {}, text: "Iets nieuws" })).toBe("Iets nieuws");
    expect(noticeText({ code: "", params: {}, text: "Oud bericht" })).toBe("Oud bericht");
  });

  it("https-fouten: op code, anders de servertekst", () => {
    expect(httpsErrorText("listener_bind", { port: 443, reason: "bezet" }, "x")).toBe(
      "De https-poort 443 gaat niet open: bezet.",
    );
    expect(httpsErrorText(null, {}, "Servertekst")).toBe("Servertekst");
    const base = { error: null, listener_error: null } as Parameters<typeof httpsErrors>[0];
    expect(httpsErrors(base)).toEqual({ error: null, listener: null });
    expect(httpsErrors({ ...base, error: "Fout", error_code: "onbekend" }).error).toBe("Fout");
  });
});

describe("quotum", () => {
  it("parseQuota: leeg = null, getal in GB, komma mag, onzin = undefined", () => {
    expect(parseQuota("")).toBeNull();
    expect(parseQuota("  ")).toBeNull();
    expect(parseQuota("0")).toBe(0);
    expect(parseQuota("2")).toBe(2 * GB);
    expect(parseQuota("1,5")).toBe(Math.round(1.5 * GB));
    expect(parseQuota("-1")).toBeUndefined();
    expect(parseQuota("veel")).toBeUndefined();
  });

  it("quotaInput: bytes terug naar GB", () => {
    expect(quotaInput(0)).toBe("0");
    expect(quotaInput(2 * GB)).toBe("2");
    expect(quotaInput(Math.round(1.5 * GB))).toBe("1.5");
  });

  it("changesOf: alleen wat anders is; leeg quotum bij wijzigen = onbeperkt", () => {
    const user = {
      id: "u",
      email: "a@x.nl",
      display_name: "An",
      role: "user",
      quota_bytes: 2 * GB,
      used_bytes: 0,
      root_id: "r",
      totp_enabled: false,
      totp_setup_required: false,
      totp_locked_until: null,
      disabled: false,
    } satisfies AdminUser;
    const form = { display_name: "An", email: "a@x.nl", password: "", role: "user" as const, quota: "2" };
    expect(changesOf(user, form)).toEqual({});
    expect(changesOf(user, { ...form, display_name: " Anna ", quota: "" })).toEqual({
      display_name: "Anna",
      quota_bytes: 0,
    });
    expect(changesOf(user, { ...form, role: "admin", email: "b@x.nl" })).toEqual({ role: "admin", email: "b@x.nl" });
    expect(changesOf(user, { ...form, quota: "x" })).toBeNull();
  });
});

describe("taken", () => {
  it("soort en onderwerp", () => {
    expect(jobTypeLabel("preview")).toBe("Preview");
    expect(jobTypeLabel("user.delete")).toBe("Gebruiker verwijderen");
    expect(jobTypeLabel("preview.outdated")).toBe("Previews opnieuw maken");
    expect(jobTypeLabel("photometa")).toBe("Fotogegevens");
    expect(jobTypeLabel("iets.anders")).toBe("iets.anders");
    expect(jobSubject("preview:0123456789abcdef")).toBe("0123456789ab…");
    expect(jobSubject("user.delete:abc")).toBe("abc");
    expect(jobSubject(null)).toBe("");
  });

  it("byTypeRows: per soort één rij, op naam", () => {
    const rows = byTypeRows({
      by_type: [
        { type: "user.delete", status: "queued", count: 1 },
        { type: "preview", status: "done", count: 7 },
        { type: "preview", status: "failed", count: 2 },
      ],
    });
    expect(rows).toEqual([
      { type: "preview", counts: { done: 7, failed: 2 } },
      { type: "user.delete", counts: { queued: 1 } },
    ]);
  });
});

describe("matchesUser", () => {
  it("op naam of e-mail, zonder hoofdletters of accenten", () => {
    const u = { display_name: "Zoë de Vries", email: "zoe@x.nl" };
    expect(matchesUser(u, "", fold)).toBe(true);
    expect(matchesUser(u, "zoe", fold)).toBe(true);
    expect(matchesUser(u, "VRIES", fold)).toBe(true);
    expect(matchesUser(u, "x.nl", fold)).toBe(true);
    expect(matchesUser(u, "jan", fold)).toBe(false);
  });
});
