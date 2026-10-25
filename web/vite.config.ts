/// <reference path="./vite.node-shim.d.ts" />
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";
import react from "@vitejs/plugin-react";
import { build, defineConfig, type Plugin, type ResolvedConfig, type Rolldown } from "vite";

// De service worker en het voorcomprimeren draaien pas na de build van de app (closeBundle):
// de service worker heeft de lijst van de schil nodig, en die staat pas in het manifest van
// de build. Zie docs/fase-3/p1.md.

/** Eén regel uit dist/.vite/manifest.json. */
interface ManifestChunk {
  file: string;
  src?: string;
  isEntry?: boolean;
  imports?: string[];
  css?: string[];
}

/** VITE_SW=off bouwt een service worker die zichzelf uitschakelt (de noodknop). */
const KILL_SWITCH = process.env.VITE_SW === "off";

/** Woordenboeken die de schil zonder netwerk nodig heeft: de basis in beide talen, de
 * offline-melding (pwa) en de Engelse foutteksten. */
const OFFLINE_I18N = /src\/i18n\/(areas\/(common|shell|auth|pwa)\.(nl|en)\.ts|errors\/[a-z]+\.ts)$/;

/** Wat de service worker vooraf ophaalt: index.html, het entry-chunk met zijn statische
 * imports en css, de woordenboeken van hierboven, boot.js, het manifest en de iconen. */
export function shellList(manifest: Record<string, ManifestChunk>, publicFiles: string[]): string[] {
  const out = new Set<string>(["/index.html"]);
  const seen = new Set<string>();
  const visit = (key: string) => {
    if (seen.has(key)) return;
    seen.add(key);
    const chunk = manifest[key];
    if (!chunk) return;
    if (!chunk.file.endsWith(".html")) out.add(`/${chunk.file}`);
    for (const css of chunk.css ?? []) out.add(`/${css}`);
    for (const dep of chunk.imports ?? []) visit(dep);
  };
  for (const [key, chunk] of Object.entries(manifest)) if (chunk.isEntry) visit(key);
  for (const [key, chunk] of Object.entries(manifest)) {
    if (OFFLINE_I18N.test(chunk.src ?? key)) visit(key);
  }
  for (const f of publicFiles) out.add(`/${f}`);
  return [...out].sort();
}

/** Alle bestanden onder dir, als pad ten opzichte van dir met "/". */
function walk(dir: string, base = dir): string[] {
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full, base));
    else out.push(relative(base, full).split("\\").join("/"));
  }
  return out;
}

/** Bestanden uit public/ die bij de schil horen (boot.js, manifest, iconen, favicon). */
const PUBLIC_SHELL = /^(boot\.js|manifest\.webmanifest|favicon\.(svg|ico)|icons\/.+)$/;

function swPlugin(): Plugin {
  let config: ResolvedConfig;
  return {
    name: "platform-sw",
    apply: "build",
    configResolved(c) {
      config = c;
    },
    closeBundle: {
      sequential: true,
      async handler() {
        const outDir = resolve(config.root, config.build.outDir);
        const manifestPath = join(outDir, ".vite", "manifest.json");
        if (!existsSync(manifestPath)) return; // geen app-build (bijv. de geneste build zelf)
        const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as Record<string, ManifestChunk>;
        const publicFiles = walk(config.publicDir).filter((f) => PUBLIC_SHELL.test(f));
        const precache = shellList(manifest, publicFiles);
        // Alle chunks van deze build: wat de cache van /assets daarbuiten heeft, gaat weg
        const assets = walk(join(outDir, "assets")).map((f) => `/assets/${f}`).sort();
        const source = readFileSync(resolve(config.root, "src/pwa/sw.ts"), "utf8");
        const hash = createHash("sha256")
          .update(readFileSync(join(outDir, "index.html")))
          .update(JSON.stringify(precache))
          .update(source)
          .update(String(KILL_SWITCH))
          .digest("hex")
          .slice(0, 12);
        // Een eigen build, los van de app: één IIFE-bestand zonder imports
        await build({
          configFile: false,
          root: config.root,
          logLevel: "warn",
          publicDir: false,
          define: {
            __PRECACHE__: JSON.stringify(precache),
            __ASSETS__: JSON.stringify(assets),
            __BUILD__: JSON.stringify(hash),
            __KILL_SWITCH__: JSON.stringify(KILL_SWITCH),
          },
          build: {
            outDir,
            emptyOutDir: false,
            copyPublicDir: false,
            minify: true,
            lib: {
              entry: resolve(config.root, "src/pwa/sw.ts"),
              formats: ["iife"],
              name: "PlatformSW",
              fileName: () => "sw.js",
            },
          },
        });
        rmSync(join(outDir, ".vite"), { recursive: true, force: true });
      },
    },
  };
}

/** Waar chunkGraph de graaf van de laatste build neerzet (buiten dist: wordt niet geserveerd). */
export const CHUNK_GRAPH = "node_modules/.tmp/platform-chunks.json";

/** De chunks van een bundel met hun modules en imports, als regels van de chunkgraaf. */
function graphOf(root: string, bundle: Rolldown.OutputBundle): Record<string, unknown> {
  const chunks: Record<string, unknown> = {};
  for (const [fileName, item] of Object.entries(bundle)) {
    if (item.type !== "chunk") continue;
    chunks[fileName] = {
      isEntry: item.isEntry,
      isDynamicEntry: item.isDynamicEntry,
      facade: item.facadeModuleId ? relative(root, item.facadeModuleId).split("\\").join("/") : null,
      modules: item.moduleIds.map((id) => relative(root, id).split("\\").join("/")),
      imports: item.imports,
      dynamicImports: item.dynamicImports,
      css: [...(item.viteMetadata?.importedCss ?? [])],
    };
  }
  return chunks;
}

/** De chunks van de web workers (new Worker(new URL(...))). Vite bouwt elke worker in een
 * eigen bundel die als asset in de app-build belandt; zonder deze regels kende de graaf
 * die bestanden niet en kon het budget de worker niet meten. */
const workerChunks: Record<string, unknown> = {};

function workerGraph(): Plugin {
  let root = "";
  return {
    name: "platform-worker-graph",
    apply: "build",
    configResolved(c) {
      root = c.root;
    },
    generateBundle(_options, bundle) {
      Object.assign(workerChunks, graphOf(root, bundle));
    },
  };
}

/** De chunks van de build met hun modules en imports, voor scripts/budget.mjs. */
function chunkGraph(): Plugin {
  let config: ResolvedConfig;
  return {
    name: "platform-chunk-graph",
    apply: "build",
    configResolved(c) {
      config = c;
    },
    buildStart() {
      // De workers bouwt Vite pas tijdens deze build: wat er van een vorige build staat weg
      for (const key of Object.keys(workerChunks)) delete workerChunks[key];
    },
    writeBundle(_options, bundle) {
      const chunks = { ...workerChunks, ...graphOf(config.root, bundle) };
      const path = resolve(config.root, CHUNK_GRAPH);
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, JSON.stringify({ outDir: config.build.outDir, chunks }, null, 1));
    },
  };
}

/** Voorgecomprimeerde .br en .gz naast elk tekstbestand boven 1 KB; de server kiest. */
function precompress(): Plugin {
  let config: ResolvedConfig;
  return {
    name: "platform-precompress",
    apply: "build",
    configResolved(c) {
      config = c;
    },
    closeBundle: {
      order: "post",
      sequential: true,
      handler() {
        const outDir = resolve(config.root, config.build.outDir);
        for (const file of walk(outDir)) {
          if (!/\.(js|css|html|svg|webmanifest|json)$/.test(file)) continue;
          const full = join(outDir, file);
          const data = readFileSync(full);
          if (data.length <= 1024) continue;
          writeFileSync(
            `${full}.br`,
            brotliCompressSync(data, {
              params: {
                [constants.BROTLI_PARAM_QUALITY]: 11,
                [constants.BROTLI_PARAM_SIZE_HINT]: data.length,
              },
            }),
          );
          writeFileSync(`${full}.gz`, gzipSync(data, { level: 9 }));
        }
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), chunkGraph(), swPlugin(), precompress()],
  worker: {
    plugins: () => [workerGraph()],
  },
  define: {
    "import.meta.env.VITE_SW": JSON.stringify(KILL_SWITCH ? "off" : "on"),
  },
  build: {
    manifest: true,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            // React verandert zelden: een vast chunk dat lang in de cache blijft
            { name: "vendor", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/, priority: 2 },
            // De rest van de eerste lading in één chunk: op een mobiel netwerk kost elk
            // verzoek een rondgang, en zonder deze groep werden het er zestien
            // (zonder index.html en main.tsx zelf: die blijven het entry-chunk)
            { name: "shell", tags: ["$initial"], test: (id) => !/(index\.html|src[\\/]main\.tsx)$/.test(id), priority: 1 },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
