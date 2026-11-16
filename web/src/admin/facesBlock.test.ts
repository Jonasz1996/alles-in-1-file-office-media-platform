import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fmt, loadAreas, resetI18nForTests } from "../i18n";
import { analyseText, FacesBlock, qualityText, queueText, speedText } from "./FacesBlock";
import type { AdminFaces } from "./searchApi";

// Het blok "Gezichten" op /beheer (fase 6, X2b; Keuze 67): de stand van de echte monitor (ok,
// down met de reden, models_missing met de namen, off), de modellen met hun versie, de grenzen,
// de rij, aantallen en "kwaliteit niet gemeten" tot app.cli analyse-kwaliteit draaide.

function faces(over: Partial<AdminFaces> = {}): AdminFaces {
  return {
    allowed: true,
    analyse: "ok",
    reason: null,
    models: [
      { name: "yunet", version: "8ab9d1c3e2f0", sha_ok: true },
      { name: "sface", version: "0c1d2e3f4a5b", sha_ok: true },
    ],
    limits: { cpus: 1.5, mem_mb: 3072 },
    users_on: 2,
    queue: { waiting: 40, done_hour: 120, waits_for: null },
    failed_by_code: [],
    faces: 1234,
    named: 56,
    db_bytes: 1234 * 800,
    bench: null,
    eta_s: null,
    wipe_waiting: false,
    quality: null,
    ...over,
  };
}

const html = (f: AdminFaces) => renderToStaticMarkup(createElement(FacesBlock, { faces: f }));

/** De tekst bij data-search="faces-<naam>". */
function row(markup: string, name: string): string | null {
  const m = new RegExp(`<dd data-search="faces-${name}">(.*?)</dd>`).exec(markup);
  return m ? m[1] : null;
}

beforeEach(async () => {
  resetI18nForTests("nl");
  await loadAreas(["common", "admin", "photos_faces"]);
});

afterEach(() => resetI18nForTests("nl"));

describe("de regel Analyse: de stand van de monitor", () => {
  it("ok, off, down met elke reden van de monitor, en models_missing met de namen", () => {
    expect(analyseText({ analyse: "ok", reason: null })).toBe("werkt");
    expect(analyseText({ analyse: "off", reason: null })).toBe("uit (ANALYSE=uit)");
    expect(analyseText({ analyse: "down", reason: "no_socket" })).toBe(
      "niet bereikbaar: de socket van de analyse is er niet (draait de container analyse?)",
    );
    expect(analyseText({ analyse: "down", reason: "key_missing" })).toBe(
      "niet bereikbaar: de sleutel PLATFORM_ANALYSE_WORKER_KEY ontbreekt",
    );
    expect(analyseText({ analyse: "down", reason: "bad_answer" })).toBe(
      "niet bereikbaar: de analyse geeft een antwoord dat niet klopt",
    );
    expect(analyseText({ analyse: "down", reason: "no_sqlite" })).toBe(
      "niet bereikbaar: de analyse werkt alleen met SQLite",
    );
    expect(analyseText({ analyse: "models_missing", reason: "yunet, sface" })).toBe(
      "een model ontbreekt of klopt niet: yunet, sface",
    );
    // Een reden die de web niet kent, staat er letterlijk; bij ok telt een reden niet
    expect(analyseText({ analyse: "down", reason: "iets_nieuws" })).toBe("niet bereikbaar: iets_nieuws");
    expect(analyseText({ analyse: "ok", reason: "no_socket" })).toBe("werkt");
  });

  it("de rij, de snelheid en de kwaliteit", () => {
    expect(queueText({ waiting: 1234, done_hour: 5, waits_for: null })).toBe("1.234 wachtend, 5 in het laatste uur");
    expect(queueText({ waiting: 3, done_hour: 0, waits_for: "transcodes" })).toBe(
      "3 wachtend, 0 in het laatste uur; wacht op het omzetten van video's",
    );
    expect(queueText({ waiting: 3, done_hour: 0, waits_for: "paused" })).toBe("3 wachtend, 0 in het laatste uur; gepauzeerd");
    expect(speedText({ bench: null, eta_s: 600 })).toBe("niet gemeten (app.cli analyse-bench)");
    const at = "2026-11-10T09:00:00+00:00";
    expect(speedText({ bench: { at, photos_per_minute: 42.25 }, eta_s: null })).toBe(
      `42,3 foto's per minuut (analyse-bench, ${fmt.dateTime(at)})`,
    );
    expect(speedText({ bench: { at, photos_per_minute: 40 }, eta_s: 3725 })).toBe(
      `40 foto's per minuut (analyse-bench, ${fmt.dateTime(at)}); nog ±1:02:05 voor wat er wacht`,
    );
    expect(qualityText(null)).toBe("niet gemeten");
    expect(qualityText({ at, ok: true })).toBe(`gemeten op ${fmt.dateTime(at)}: de poorten gehaald`);
    expect(qualityText({ at, ok: false })).toBe(`gemeten op ${fmt.dateTime(at)}: de poorten niet gehaald`);
  });
});

describe("het blok met een stand (Keuze 67)", () => {
  it("de regels in volgorde, de schakelaar, de zin over reservekopieën en Mislukte opnieuw", () => {
    const markup = html(faces());
    const labels = [...markup.matchAll(/<dt>(.*?)<\/dt>/g)].map((m) => m[1]);
    expect(labels).toEqual([
      "Analyse",
      "Modellen",
      "Grenzen",
      "Gebruikers met gezichten aan",
      "Rij analyse",
      "Mislukt",
      "Totaal",
      "Snelheid",
      "Kwaliteit",
    ]);
    expect(row(markup, "analyse")).toBe("werkt");
    expect(row(markup, "models")).toBe(
      "yunet, versie 8ab9d1c3e2f0: sha256 klopt; sface, versie 0c1d2e3f4a5b: sha256 klopt",
    );
    expect(row(markup, "limits")).toBe("1,5 cpu&#x27;s, 3,0 GB geheugen");
    expect(row(markup, "users")).toBe("2");
    expect(row(markup, "queue")).toBe("40 wachtend, 120 in het laatste uur");
    expect(row(markup, "failed")).toBe("niets");
    expect(row(markup, "total")).toBe("1.234 gezichten, waarvan 56 benoemd; ±964,1 KB in de database");
    expect(row(markup, "quality")).toBe("niet gemeten");
    expect(row(markup, "wipe")).toBeNull();
    expect(markup).toContain(
      '<label class="check"><input type="checkbox" data-search="faces-allowed" checked=""/>Gezichtsherkenning toestaan</label>',
    );
    expect(markup).toContain(
      "Reservekopieën die het platform maakt, bevatten geen gezichten. Maak je zelf snapshots van /data (ZFS, restic), dan staan ze daar versleuteld in.",
    );
    expect(markup).toMatch(/<button type="button" class="btn" data-search="faces-retry">Mislukte opnieuw<\/button>/);
    // Nooit een naam van een gebruiker of een gezicht; geen sleutel van het woordenboek
    expect(markup).not.toMatch(/photos_faces\.|\{[a-z]+\}/);
  });

  it("zonder grenzen, met mislukt per code, met wissen dat wacht en verboden", () => {
    const markup = html(
      faces({
        allowed: false,
        analyse: "models_missing",
        reason: "sface",
        models: [
          { name: "yunet", version: "8ab9d1c3e2f0", sha_ok: true },
          { name: "sface", version: "?", sha_ok: false },
        ],
        limits: null,
        failed_by_code: [
          { code: "image_bad", n: 2 },
          { code: "timeout", n: 1 },
        ],
        wipe_waiting: true,
      }),
    );
    expect(row(markup, "analyse")).toBe("een model ontbreekt of klopt niet: sface");
    expect(row(markup, "models")).toBe("yunet, versie 8ab9d1c3e2f0: sha256 klopt; sface, versie ?: sha256 klopt niet");
    expect(row(markup, "limits")).toBe("zonder grenzen");
    expect(row(markup, "failed")).toBe("image_bad 2, timeout 1");
    expect(row(markup, "wipe")).toBe("wissen wacht op een lezer");
    expect(markup).toContain('<input type="checkbox" data-search="faces-allowed"/>');
  });

  it("zonder caps (down): geen modellen bekend", () => {
    const markup = html(faces({ analyse: "down", reason: "refused", models: [], limits: null }));
    expect(row(markup, "analyse")).toBe("niet bereikbaar: de analyse weigert de verbinding");
    expect(row(markup, "models")).toBe("geen (de app weet niet wat er in de container staat)");
  });

  it("null (de analyse staat uit): de kop en één zin (uit, en wat de beheerder doet), zonder knop of schakelaar", () => {
    expect(renderToStaticMarkup(createElement(FacesBlock, { faces: null }))).toBe(
      '<section aria-labelledby="admin-faces-title"><h3 id="admin-faces-title">Gezichten</h3>' +
        '<p class="muted">Gezichten herkennen staat uit op deze server: de analyse draait niet (ANALYSE=uit in /opt/platform/.env). Aanzetten: draai het installatiescript opnieuw met ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto&#x27;s. Zie docs/runbooks/analyse.md.</p></section>',
    );
  });

  it("en: dezelfde regels in het Engels, zonder open plek", async () => {
    resetI18nForTests("en");
    await loadAreas(["common", "admin", "photos_faces"]);
    const markup = html(faces({ analyse: "down", reason: "no_answer", limits: null }));
    expect(row(markup, "analyse")).toBe("not reachable: the analysis does not answer within 5 s");
    expect(row(markup, "quality")).toBe("not measured");
    expect(row(markup, "limits")).toBe("no limits");
    expect(markup).not.toMatch(/photos_faces\.|\{[a-z]+\}/);
  });
});
