#!/usr/bin/env node
// Het groottebudget van de web-app (bouwontwerp §4.1), een harde poort in CI. Draait na
// `npm run build`: gzipt elk bestand in dist (niveau 9) en volgt de importgraaf vanaf
// index.html. De graaf (welke modules in welk chunk zitten, en wie wie importeert) schrijft
// vite.config.ts bij de build naar node_modules/.tmp/platform-chunks.json.
//
// Faalt (exitcode 1) als:
// - een grens hieronder overschreden wordt;
// - dist/sw.js een import of export bevat (een service worker in klassieke vorm kan dat niet);
// - een woordenboek van een ander gebied dan common, shell of auth (nl) in de eerste lading zit;
// - een module van media, de speler of muziek (fase 5) in de eerste lading zit, behalve de
//   kleine ingang src/media/index.ts (Keuze 33).
//
// Fase 5 (M0): drie regels erbij (Keuze 33). Elk telt wat er bij het openen bovenop de eerste
// lading binnenkomt: de chunks met hun statische imports, en het Nederlandse woordenboek.
// - mediaRoute: elke mediapagina apart (/media, bibliotheek, item, serie en /kijken);
// - player: alles met een module uit src/player/ samen (de speler, ook in de viewer);
// - music: alles met een module uit src/music/ samen (pagina's, gapless en de balk).
//
// Gebruik: node scripts/budget.mjs [--json]

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const WEB = fileURLToPath(new URL("..", import.meta.url));
const DIST = join(WEB, "dist");
const GRAPH = join(WEB, "node_modules/.tmp/platform-chunks.json");
const KB = 1000;

/** De grenzen in bytes gzip (§4.1). */
export const LIMITS = {
  initialJs: 100 * KB,
  filesRoute: 45 * KB,
  lazyChunk: 50 * KB,
  heavyChunk: 60 * KB,
  initialCss: 12 * KB,
  sw: 8 * KB,
  // Fase 5 (Keuze 33)
  mediaRoute: 45 * KB,
  player: 35 * KB,
  music: 40 * KB,
};

/** Woordenboeken die in de eerste lading mogen (statisch in i18n/index.ts). */
const INITIAL_AREAS = /^src\/i18n\/areas\/(common|shell|auth)\.nl\.ts$/;
const AREA = /^src\/i18n\/areas\/(\w+)\.(nl|en)\.ts$/;
/** Woordenboeken die bij de route Bestanden horen. */
const FILES_AREAS = /^src\/i18n\/areas\/(browser|upload|sharing)\.nl\.ts$/;
/** De pagina's van media (routes.tsx) met hun woordenboeken (fase 5). */
export const MEDIA_PAGES = {
  "src/media/LibraryHome.tsx": ["media"],
  "src/media/Library.tsx": ["media"],
  "src/media/ItemPage.tsx": ["media"],
  "src/media/ShowPage.tsx": ["media"],
  "src/player/WatchPage.tsx": ["player", "media"],
};
/** Modules van fase 5 die nooit in de eerste lading horen (alleen media/index.ts wel). */
const MEDIA_MODULE = /^src\/(media|player|music)\//;
const MEDIA_ENTRY = "src/media/index.ts";
/** Een highlighter of worker mag groter zijn. */
const HEAVY = /(\.worker\.ts|highlight)/i;

function fail(message) {
  console.error(`budget: ${message}`);
  process.exit(1);
}

function files(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...files(full));
    else out.push(relative(DIST, full).split("\\").join("/"));
  }
  return out;
}

const gzCache = new Map();
function gz(file) {
  let size = gzCache.get(file);
  if (size === undefined) {
    size = gzipSync(readFileSync(join(DIST, file)), { level: 9 }).length;
    gzCache.set(file, size);
  }
  return size;
}

function kb(n) {
  return `${(n / KB).toFixed(1)} KB`;
}

/** De statische imports van index.html: module-scripts, modulepreloads en stylesheets. */
export function htmlRoots(html) {
  const js = [];
  const css = [];
  for (const tag of html.match(/<(script|link)\b[^>]*>/gi) ?? []) {
    const src = /\b(?:src|href)="\/?([^"]+)"/i.exec(tag)?.[1];
    if (!src || !src.startsWith("assets/")) continue;
    if (/^<script/i.test(tag) && /type="module"/i.test(tag)) js.push(src);
    else if (/rel="modulepreload"/i.test(tag)) js.push(src);
    else if (/rel="stylesheet"/i.test(tag)) css.push(src);
  }
  return { js, css };
}

/** Alle chunks die vanaf roots statisch geladen worden. */
export function closure(chunks, roots) {
  const seen = new Set();
  const stack = [...roots];
  while (stack.length) {
    const file = stack.pop();
    if (seen.has(file) || !chunks[file]) continue;
    seen.add(file);
    stack.push(...chunks[file].imports);
  }
  return seen;
}

/** Een top-level import of export, of een dynamische import(): in sw.js verboden. */
export function hasModuleSyntax(code) {
  return /(^|[;{}\n])\s*(import\b\s*[\w{*"']|export\b\s*[\w{*])/.test(code) || /\bimport\s*\(/.test(code);
}

function main() {
  const asJson = process.argv.includes("--json");
  if (!existsSync(join(DIST, "index.html"))) fail("geen dist/index.html; eerst `npm run build`");
  if (!existsSync(GRAPH)) fail(`geen ${relative(WEB, GRAPH)}; eerst \`npm run build\``);
  const { chunks } = JSON.parse(readFileSync(GRAPH, "utf8"));
  const distFiles = new Set(files(DIST));
  // De graaf moet bij deze dist horen
  const jsInDist = [...distFiles].filter((f) => /^assets\/.+\.js$/.test(f));
  const stale = Object.keys(chunks).filter((f) => !distFiles.has(f));
  const unknown = jsInDist.filter((f) => !chunks[f]);
  if (stale.length || unknown.length) {
    fail(`de chunkgraaf hoort niet bij deze dist (${[...stale, ...unknown].slice(0, 3).join(", ")}); opnieuw bouwen`);
  }

  const html = readFileSync(join(DIST, "index.html"), "utf8");
  const roots = htmlRoots(html);
  const initial = closure(chunks, roots.js);
  const initialCss = new Set(roots.css);
  for (const f of initial) for (const c of chunks[f].css) initialCss.add(c);

  const problems = [];
  const rows = [];
  const check = (name, size, limit, detail = "") => {
    const ok = size <= limit;
    rows.push({ name, size, limit, ok, detail });
    if (!ok) problems.push(`${name}: ${kb(size)} > ${kb(limit)}${detail ? ` (${detail})` : ""}`);
  };

  // 1. De eerste lading
  const initialJs = [...initial].reduce((sum, f) => sum + gz(f), 0);
  check("eerste lading JS", initialJs, LIMITS.initialJs, [...initial].sort().join(", "));
  const cssSize = [...initialCss].reduce((sum, f) => sum + gz(f), 0);
  check("eerste lading CSS", cssSize, LIMITS.initialCss, [...initialCss].sort().join(", "));

  // Geen woordenboek van een ander gebied in de eerste lading
  for (const f of initial) {
    for (const m of chunks[f].modules) {
      if (AREA.test(m) && !INITIAL_AREAS.test(m)) problems.push(`woordenboek ${m} zit in de eerste lading (${f})`);
    }
  }

  // 2. De route Bestanden: zijn chunk, wat hij daarnaast statisch laadt, en zijn woordenboeken
  const facade = (re) => Object.keys(chunks).filter((f) => chunks[f].facade && re.test(chunks[f].facade));
  const filesRoots = facade(/^src\/Files\.tsx$/);
  const filesGroup = new Set();
  if (filesRoots.length) {
    for (const f of closure(chunks, filesRoots)) if (!initial.has(f)) filesGroup.add(f);
    for (const f of facade(FILES_AREAS)) if (!initial.has(f)) filesGroup.add(f);
    const size = [...filesGroup].reduce((sum, f) => sum + gz(f), 0);
    check("route Bestanden", size, LIMITS.filesRoute, [...filesGroup].sort().join(", "));
  } else {
    problems.push("geen chunk voor src/Files.tsx gevonden");
  }

  // 3. Fase 5: elke mediapagina, de speler en muziek (Keuze 33)
  for (const f of initial) {
    for (const m of chunks[f].modules) {
      if (MEDIA_MODULE.test(m) && m !== MEDIA_ENTRY) problems.push(`${m} zit in de eerste lading (${f})`);
    }
  }
  const dict = (area) => facade(new RegExp(`^src/i18n/areas/${area}\\.nl\\.ts$`));
  /** De chunks bovenop de eerste lading: de closure van roots en de woordenboeken. */
  const extra = (roots, areas) => {
    const out = new Set();
    for (const f of closure(chunks, roots)) if (!initial.has(f)) out.add(f);
    for (const area of areas) for (const f of dict(area)) if (!initial.has(f)) out.add(f);
    return out;
  };
  const sum = (set) => [...set].reduce((total, f) => total + gz(f), 0);
  for (const [page, areas] of Object.entries(MEDIA_PAGES)) {
    const roots = facade(new RegExp(`^${page.replace(/[.]/g, "\\.")}$`));
    if (!roots.length) {
      problems.push(`geen chunk voor ${page} gevonden`);
      continue;
    }
    const group = extra(roots, areas);
    check(`mediapagina ${page.replace(/^src\//, "")}`, sum(group), LIMITS.mediaRoute, [...group].sort().join(", "));
  }
  const containing = (re) => Object.keys(chunks).filter((f) => chunks[f].modules.some((m) => re.test(m)));
  for (const [name, re, area, limit] of [
    ["speler (src/player)", /^src\/player\//, "player", LIMITS.player],
    ["muziek (src/music)", /^src\/music\//, "music", LIMITS.music],
  ]) {
    const roots = containing(re);
    if (!roots.length) {
      problems.push(`geen chunk met ${name} gevonden`);
      continue;
    }
    const group = extra(roots, [area]);
    check(name, sum(group), limit, [...group].sort().join(", "));
  }

  // 4. Elk ander lazy chunk apart
  let lazyCount = 0;
  let largest = { file: null, size: 0 };
  for (const f of Object.keys(chunks).sort()) {
    if (initial.has(f) || filesGroup.has(f)) continue;
    const heavy = HEAVY.test(chunks[f].facade ?? "") || chunks[f].modules.some((m) => HEAVY.test(m));
    const size = gz(f);
    lazyCount++;
    if (size > largest.size) largest = { file: f, size };
    if (heavy) check(`highlighter/worker ${f}`, size, LIMITS.heavyChunk);
    else if (size > LIMITS.lazyChunk) check(`lazy chunk ${f}`, size, LIMITS.lazyChunk, chunks[f].facade ?? "");
  }
  rows.push({
    name: `grootste lazy chunk (van ${lazyCount})`,
    size: largest.size,
    limit: LIMITS.lazyChunk,
    ok: largest.size <= LIMITS.lazyChunk,
    detail: largest.file ?? "",
  });

  // 5. De service worker
  if (!distFiles.has("sw.js")) {
    problems.push("dist/sw.js ontbreekt");
  } else {
    const code = readFileSync(join(DIST, "sw.js"), "utf8");
    if (hasModuleSyntax(code)) problems.push("dist/sw.js bevat een import of export");
    check("sw.js", gz("sw.js"), LIMITS.sw);
  }

  if (asJson) {
    console.log(JSON.stringify({ ok: problems.length === 0, rows, problems }, null, 2));
  } else {
    for (const r of rows) {
      const mark = r.ok ? "ok  " : "FOUT";
      console.log(`${mark} ${r.name.padEnd(42)} ${kb(r.size).padStart(9)} / ${kb(r.limit)}`);
    }
    for (const p of problems) console.error(`FOUT ${p}`);
    console.log(problems.length ? `budget: ${problems.length} probleem/problemen` : "budget: binnen het budget");
  }
  process.exit(problems.length ? 1 : 0);
}

// Alleen als script (de functies hierboven zijn ook te importeren)
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
