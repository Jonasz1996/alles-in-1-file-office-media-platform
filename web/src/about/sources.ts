// De bronnen op /over (fase 6, I2): namen, versies, licenties en adressen, in elke taal hetzelfde.
// Vaste gegevens, geen aanroep: /over toont hetzelfde met de analyse aan of uit, en niets van een
// gebruiker. about.test.ts legt elke waarde naast haar bron in de repo: GET /geo/attribution
// (api/app/routers/geo.py en api/app/geo/load.py), api/app/analyse/models.lock, de map
// api/app/analyse/licenties/ en de extra analyse in api/uv.lock (wat de Dockerfile in /opt/analyse
// zet).

/** De gegevens van de kaart (Keuze 29): zoals GET /geo/attribution en de README (Bronnen). */
export const GEO = {
  places: {
    source: "GeoNames cities1000",
    file: "rg_cities1000.csv",
    from: "reverse_geocoder 1.5.1",
    license: "CC BY 4.0",
  },
  countries: { source: "Natural Earth 1:10m admin-0", version: "v5.1.2" },
  world: { source: "Natural Earth 1:110m admin-0" },
} as const;

export interface SourceLink {
  /** Wat de link toont: het adres zonder https:// */
  text: string;
  url: string;
}

function link(url: string): SourceLink {
  return { text: url.replace(/^https:\/\//, "").replace(/\/$/, ""), url };
}

export const GEO_LINKS: readonly SourceLink[] = [
  link("https://www.geonames.org/"),
  link("https://creativecommons.org/licenses/by/4.0/"),
  link("https://www.naturalearthdata.com/"),
];

/** De modellen van de analyse (K1-13): YuNet en SFace (X2), SigLIP 2 (X3). */
export const MODELS = {
  yunet: {
    name: "YuNet 2026may",
    from: "opencv_zoo",
    license: "MIT",
    notice: "Copyright (c) 2020 Shiqi Yu",
    file: "yunet-MIT.txt",
  },
  sface: { name: "SFace 2021dec", from: "opencv_zoo", license: "Apache-2.0", file: "sface-Apache-2.0.txt" },
  siglip: {
    name: "SigLIP 2 B/32-256",
    from: "Google (google/siglip2-base-patch32-256)",
    license: "Apache-2.0",
    file: "siglip2-Apache-2.0.txt",
  },
} as const;

export const MODEL_LINKS: readonly SourceLink[] = [
  link("https://github.com/opencv/opencv_zoo"),
  link("https://huggingface.co/google/siglip2-base-patch32-256"),
];

/** Waar de licentieteksten staan: naast de modellen (MODELLEN_ROOT, standaard) en in de repo. */
export const LICENSE_PLACES = {
  models: "/opt/platform/modellen",
  folder: "api/app/analyse/licenties/",
} as const;

/** De pakketten in /opt/analyse: onnxruntime en wat ermee meekomt (de extra analyse van
 * api/uv.lock zonder wat de app zelf al heeft; de Dockerfile telt er precies vijf). */
export const PACKAGES = [
  { name: "onnxruntime", version: "1.31.1", license: "MIT" },
  { name: "numpy", version: "2.5.4", license: "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0" },
  { name: "protobuf", version: "7.36.2", license: "BSD-3-Clause" },
  { name: "flatbuffers", version: "25.12.19", license: "Apache-2.0" },
  { name: "packaging", version: "26.3", license: "Apache-2.0 OR BSD-2-Clause" },
] as const;
