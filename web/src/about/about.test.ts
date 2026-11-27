// /over (fase 6, I2; Keuze 29 en K1-13): de route, de pagina in nl en en, de link op /account, en
// elke naam, versie en licentie naast haar bron in de repo. De pagina vraagt de server niets en toont
// niets van een gebruiker.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import accountSource from "../Account.tsx?raw";
import { loadAreas, resetI18nForTests, t } from "../i18n";
import { allowed, resolve } from "../routes";
import routesSource from "../routes.tsx?raw";
import AboutPage, { mapLines, modelLines, packageLines } from "./AboutPage";
import aboutSource from "./AboutPage.tsx?raw";
import { GEO, GEO_LINKS, LICENSE_PLACES, MODEL_LINKS, MODELS, PACKAGES } from "./sources";
import sourcesSource from "./sources.ts?raw";
import apiGeoLoad from "../../../api/app/geo/load.py?raw";
import apiGeoRouter from "../../../api/app/routers/geo.py?raw";
import modelsLock from "../../../api/app/analyse/models.lock?raw";
import uvLock from "../../../api/uv.lock?raw";
import dockerfile from "../../../Dockerfile?raw";

// De licentieteksten van de modellen, precies de bestanden in de map (test_models_lock.py pint ze
// aan de API-kant)
const LICENSES = import.meta.glob("../../../api/app/analyse/licenties/*", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const NL_MAP = [
  "Plaatsnamen: GeoNames cities1000 (als rg_cities1000.csv uit reverse_geocoder 1.5.1), CC BY 4.0.",
  "Landen: Natural Earth 1:10m admin-0 v5.1.2 (publiek domein).",
  "De wereldkaart: Natural Earth 1:110m admin-0 (publiek domein).",
];
const NL_MODELS = [
  "Gezichten vinden: YuNet 2026may van opencv_zoo, MIT, Copyright (c) 2020 Shiqi Yu.",
  "Gezichten herkennen: SFace 2021dec van opencv_zoo, Apache-2.0.",
  "Zoeken op beeld: SigLIP 2 B/32-256 van Google (google/siglip2-base-patch32-256), Apache-2.0.",
];
const PACKAGE_LINES = [
  "onnxruntime 1.31.1: MIT",
  "numpy 2.5.4: BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
  "protobuf 7.36.2: BSD-3-Clause",
  "flatbuffers 25.12.19: Apache-2.0",
  "packaging 26.3: Apache-2.0 OR BSD-2-Clause",
];

/** De tekst van een stuk HTML zoals een lezer hem ziet (entiteiten terug). */
function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

afterEach(() => {
  resetI18nForTests("nl");
});

describe("de route /over", () => {
  it("een eigen lazy pagina met het gebied about, voor iedereen die is ingelogd (ook een gast)", async () => {
    const r = resolve("/over");
    expect(r.def).toMatchObject({ pattern: "/over", areas: ["about"], title: "about.title", section: null });
    expect(r.def.roles).toBeUndefined();
    for (const role of ["admin", "user", "guest"] as const) expect(allowed(r.def, role)).toBe(true);
    const page = await r.def.load();
    expect(page.default).toBe(AboutPage);
    expect(routesSource).toContain('route("/over", () => import("./about/AboutPage"), ["about"], "about.title", null),');
    // Geen poort op de analyse: de route hangt nergens van ANALYSE of BEELD af
    expect(resolve("/over/x").def.pattern).toBe("*");
  });

  it("/account laadt het gebied about mee (de link onderaan)", () => {
    expect(resolve("/account").def.areas).toEqual(["account", "about"]);
  });
});

describe("de pagina /over", () => {
  it("nl: de kaart, de modellen en de pakketten, altijd, met de licenties", async () => {
    resetI18nForTests("nl");
    await loadAreas(["about"]);
    expect(mapLines()).toEqual(NL_MAP);
    expect(modelLines()).toEqual(NL_MODELS);
    expect(packageLines()).toEqual(PACKAGE_LINES);
    const html = renderToStaticMarkup(createElement(AboutPage, { params: {} }));
    const seen = text(html);
    expect(html).toContain("<h1");
    expect(seen).toContain("Over dit platform");
    for (const line of [...NL_MAP, ...NL_MODELS, ...PACKAGE_LINES]) expect(html).toContain(`<li>${line}</li>`);
    expect(seen).toContain(
      "De volledige licentieteksten staan naast de modellen (standaard in /opt/platform/modellen) en in de broncode, in api/app/analyse/licenties/.",
    );
    expect(seen).toContain("Het platform zet SigLIP 2 op deze server zelf om naar int8");
    for (const l of [...GEO_LINKS, ...MODEL_LINKS]) expect(html).toContain(`<a href="${l.url}" rel="noreferrer">${l.text}</a>`);
    // De kopjes als regio's (h2 met aria-labelledby)
    for (const id of ["about-map", "about-analyse", "about-packages"]) {
      expect(html).toContain(`<section aria-labelledby="${id}"><h2 id="${id}">`);
    }
  });

  it("en: dezelfde regels in het Engels, met dezelfde namen en licenties", async () => {
    resetI18nForTests("en");
    await loadAreas(["about"]);
    expect(mapLines()).toEqual([
      "Place names: GeoNames cities1000 (as rg_cities1000.csv from reverse_geocoder 1.5.1), CC BY 4.0.",
      "Countries: Natural Earth 1:10m admin-0 v5.1.2 (public domain).",
      "The world map: Natural Earth 1:110m admin-0 (public domain).",
    ]);
    expect(modelLines()).toEqual([
      "Finding faces: YuNet 2026may from opencv_zoo, MIT, Copyright (c) 2020 Shiqi Yu.",
      "Recognising faces: SFace 2021dec from opencv_zoo, Apache-2.0.",
      "Search by image: SigLIP 2 B/32-256 from Google (google/siglip2-base-patch32-256), Apache-2.0.",
    ]);
    expect(packageLines()).toEqual(PACKAGE_LINES);
    const seen = text(renderToStaticMarkup(createElement(AboutPage, { params: {} })));
    expect(seen).toContain("About this platform");
    expect(seen).toContain("Faces and search by image");
  });

  it("vraagt de server niets en toont niets van een gebruiker", () => {
    for (const src of [aboutSource, sourcesSource]) {
      expect(src).not.toMatch(/from "\.\.\/(api|currentUser|events)"/);
      expect(src).not.toMatch(/\bfetch\s*\(|useCurrentUser|useEffect|useState/);
    }
    // Alleen i18n, de router-typen, PageTitle en de vaste bronnen
    const imports = [...aboutSource.matchAll(/from "([^"]+)"/g)].map((m) => m[1]).sort();
    expect(imports).toEqual(["../i18n", "../router", "../ui/PageTitle", "./sources", "react"]);
  });
});

describe("de bronnen naast de repo", () => {
  it("de kaart: precies wat GET /geo/attribution zegt (routers/geo.py en geo/load.py)", () => {
    expect(apiGeoRouter).toContain(`PlacesSource(source="${GEO.places.source}", license="${GEO.places.license}"`);
    expect(apiGeoRouter).toContain(`source="${GEO.countries.source}", license="publiek domein", version=load.NE_VERSIE`);
    expect(apiGeoRouter).toContain(`WorldSource(source="${GEO.world.source}", license="publiek domein")`);
    expect(apiGeoLoad).toContain(`NE_VERSIE = "${GEO.countries.version}"`);
    // Het bestand en het pakket waar de plaatsen uit komen
    expect(apiGeoLoad).toContain(`CSV_IN_TAR = "${GEO.places.from.replace(" ", "-")}/reverse_geocoder/${GEO.places.file}"`);
  });

  it("de modellen: de namen uit models.lock, de licenties uit api/app/analyse/licenties/", () => {
    expect(modelsLock).toContain("face_detection_yunet_2026may.onnx");
    expect(modelsLock).toContain("face_recognition_sface_2021dec.onnx");
    expect(modelsLock).toContain("https://huggingface.co/google/siglip2-base-patch32-256/resolve/");
    expect(modelsLock).toContain("YuNet 2026may (MIT, Copyright (c) 2020 Shiqi Yu) en SFace 2021dec fp32 (Apache-2.0)");
    expect(modelsLock).toContain("opencv/opencv_zoo/");
    const files = Object.keys(LICENSES)
      .map((k) => k.slice(k.lastIndexOf("/") + 1))
      .sort();
    expect(files).toEqual([MODELS.sface.file, MODELS.siglip.file, MODELS.yunet.file].sort());
    const byName = (name: string) => LICENSES[Object.keys(LICENSES).find((k) => k.endsWith(`/${name}`)) ?? ""];
    expect(byName(MODELS.yunet.file)).toContain(`${MODELS.yunet.notice} <`);
    expect(byName(MODELS.yunet.file)).toMatch(/^MIT License/);
    for (const m of [MODELS.sface, MODELS.siglip]) expect(byName(m.file)).toContain("Apache License");
    expect(LICENSE_PLACES.folder).toBe("api/app/analyse/licenties/");
  });

  it("de pakketten: de extra analyse van uv.lock zonder de app (wat de Dockerfile in /opt/analyse zet)", () => {
    // De extra analyse is numpy en onnxruntime; onnxruntime brengt flatbuffers, numpy, packaging en
    // protobuf mee. De Dockerfile telt er precies vijf
    expect(uvLock).toMatch(/\nanalyse = \[\n {4}\{ name = "numpy" \},\n {4}\{ name = "onnxruntime" \},\n\]/);
    const ort = /\nname = "onnxruntime"\n[\s\S]*?\ndependencies = \[\n([\s\S]*?)\n\]/.exec(uvLock)?.[1] ?? "";
    const deps = [...ort.matchAll(/name = "([^"]+)"/g)].map((m) => m[1]).sort();
    expect(deps).toEqual(["flatbuffers", "numpy", "packaging", "protobuf"]);
    expect(PACKAGES.map((p) => p.name).sort()).toEqual(["onnxruntime", ...deps].sort());
    expect(dockerfile).toContain('test "$(wc -l < /tmp/analyse.txt)" -eq 5');
    for (const p of PACKAGES) expect(uvLock).toContain(`\nname = "${p.name}"\nversion = "${p.version}"\n`);
  });
});

describe("de link op /account", () => {
  it("onderaan, in het deel na 2FA, met het gebied about", async () => {
    expect(accountSource).toContain(
      '<section aria-labelledby="account-about">\n' +
        '            <h2 id="account-about">{t("about.account.title")}</h2>\n' +
        "            <p>\n" +
        '              <Link to="/over">{t("about.account.link")}</Link>\n' +
        "            </p>\n" +
        "            <p>\n" +
        '              <Link to="/apps">{t("about.apps.link")}</Link>\n' +
        "            </p>\n" +
        "          </section>",
    );
    // Na Apparaten en sessies, binnen {!user.totp_setup_required && ( … )}: zolang 2FA nog aan moet,
    // toont de schil alleen /account
    const sessions = accountSource.indexOf("<Sessions reload=");
    const about = accountSource.indexOf('aria-labelledby="account-about"');
    const end = accountSource.indexOf("</main>");
    expect(sessions).toBeGreaterThan(0);
    expect(about).toBeGreaterThan(sessions);
    expect(end).toBeGreaterThan(about);
    expect(accountSource.slice(sessions, end)).not.toContain(")}\n      {");
    resetI18nForTests("nl");
    await loadAreas(["about"]);
    expect([t("about.account.title"), t("about.account.link")]).toEqual(["Over dit platform", "Bronnen en licenties"]);
    resetI18nForTests("en");
    await loadAreas(["about"]);
    expect([t("about.account.title"), t("about.account.link")]).toEqual(["About this platform", "Sources and licences"]);
  });
});
