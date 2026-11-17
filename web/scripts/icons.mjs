#!/usr/bin/env node
// Tekent het logo en schrijft de iconen en het manifest van de PWA naar public/ (P1). Zonder
// bibliotheken: de vormen worden hier zelf gerasterd (4×4 supersampling), PNG met node:zlib
// (deflate en zlib.crc32), ICO met PNG's erin. De kleuren komen uit src/tokens.css (licht).
// De uitkomst staat in git; opnieuw draaien na een wijziging aan het logo of de tokens:
//
//   node scripts/icons.mjs

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { crc32, deflateSync } from "node:zlib";

const WEB = fileURLToPath(new URL("..", import.meta.url));
const PUBLIC = join(WEB, "public");

/** Een token uit de lichte :root van tokens.css, bijv. "--accent" → "#1d5fd1". */
function token(name) {
  const css = readFileSync(join(WEB, "src/tokens.css"), "utf8");
  const root = css.slice(css.indexOf(":root"), css.indexOf("}", css.indexOf(":root")));
  const m = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(root);
  if (!m) throw new Error(`token ${name} niet gevonden in tokens.css`);
  return m[1].toLowerCase();
}

const ACCENT = token("--accent");
const ON_ACCENT = token("--accent-fg");
const BG = token("--bg");

function rgb(hex) {
  return [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16));
}

// --- het logo, in een vak van 512 ---
// Een map (tab en lichaam) met een afspeelknop erin: bestanden en media.

/** Afgeronde rechthoek. */
function roundRect(x0, y0, x1, y1, r) {
  return (x, y) => {
    if (x < x0 || x > x1 || y < y0 || y > y1) return false;
    const cx = Math.min(Math.max(x, x0 + r), x1 - r);
    const cy = Math.min(Math.max(y, y0 + r), y1 - r);
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
  };
}

/** Driehoek (punten met de klok mee). */
function triangle(a, b, c) {
  const side = (p, q, x, y) => (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0]);
  return (x, y) => side(a, b, x, y) >= 0 && side(b, c, x, y) >= 0 && side(c, a, x, y) >= 0;
}

const GLYPH = {
  tab: [112, 136, 248, 300, 28],
  body: [112, 176, 400, 384, 32],
  play: [
    [224, 228],
    [316, 280],
    [224, 332],
  ],
};

/** De lagen van onder naar boven: [vorm, kleur]. full: achtergrond tot de rand (maskable,
 * apple-touch-icon), anders een afgerond vlak met doorzichtige hoeken. scale: het symbool
 * kleiner (de veilige zone van een maskable icoon is een cirkel van 80 %). */
function layers({ full, scale }) {
  const s = (v) => 256 + (v - 256) * scale;
  const rr = ([x0, y0, x1, y1, r]) => roundRect(s(x0), s(y0), s(x1), s(y1), r * scale);
  return [
    [full ? () => true : roundRect(0, 0, 511.999, 511.999, 112), ACCENT],
    [rr(GLYPH.tab), ON_ACCENT],
    [rr(GLYPH.body), ON_ACCENT],
    [triangle(...GLYPH.play.map(([x, y]) => [s(x), s(y)])), ACCENT],
  ];
}

/** RGBA-pixels van size×size. */
function raster(size, opts) {
  const ls = layers(opts).map(([shape, hex]) => [shape, rgb(hex)]);
  const out = new Uint8Array(size * size * 4);
  const N = 4; // 4×4 monsters per pixel
  const unit = 512 / size;
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      for (let sy = 0; sy < N; sy++) {
        for (let sx = 0; sx < N; sx++) {
          const x = (px + (sx + 0.5) / N) * unit;
          const y = (py + (sy + 0.5) / N) * unit;
          let color = null;
          for (const [shape, c] of ls) if (shape(x, y)) color = c;
          if (color) {
            r += color[0];
            g += color[1];
            b += color[2];
            a++;
          }
        }
      }
      const i = (py * size + px) * 4;
      if (a) {
        out[i] = Math.round(r / a);
        out[i + 1] = Math.round(g / a);
        out[i + 2] = Math.round(b / a);
      }
      out[i + 3] = Math.round((a / (N * N)) * 255);
    }
  }
  return out;
}

// --- PNG en ICO ---

function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])) >>> 0, 0);
  return Buffer.concat([head, data, crc]);
}

export function png(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bits per kanaal
  ihdr[9] = 6; // RGBA
  // compressie, filter en interlace: 0
  const rows = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    rows[y * (size * 4 + 1)] = 0; // filter "geen"
    Buffer.from(rgba.buffer, y * size * 4, size * 4).copy(rows, y * (size * 4 + 1) + 1);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(rows, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** ICO met PNG-afbeeldingen (zo lezen alle huidige browsers het). */
function ico(images) {
  const head = Buffer.alloc(6 + 16 * images.length);
  head.writeUInt16LE(0, 0);
  head.writeUInt16LE(1, 2); // icoon
  head.writeUInt16LE(images.length, 4);
  let offset = head.length;
  images.forEach(({ size, data }, i) => {
    const e = 6 + 16 * i;
    head[e] = size >= 256 ? 0 : size;
    head[e + 1] = size >= 256 ? 0 : size;
    head.writeUInt16LE(1, e + 4); // vlakken
    head.writeUInt16LE(32, e + 6); // bits per pixel
    head.writeUInt32LE(data.length, e + 8);
    head.writeUInt32LE(offset, e + 12);
    offset += data.length;
  });
  return Buffer.concat([head, ...images.map((i) => i.data)]);
}

function svg({ full, scale }) {
  const s = (v) => +(256 + (v - 256) * scale).toFixed(2);
  const rect = ([x0, y0, x1, y1, r]) =>
    `<rect x="${s(x0)}" y="${s(y0)}" width="${s(x1) - s(x0)}" height="${s(y1) - s(y0)}" rx="${+(r * scale).toFixed(2)}"/>`;
  const points = GLYPH.play.map(([x, y]) => `${s(x)},${s(y)}`).join(" ");
  const bg = full
    ? `<rect width="512" height="512" fill="${ACCENT}"/>`
    : `<rect width="512" height="512" rx="112" fill="${ACCENT}"/>`;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">${bg}` +
    `<g fill="${ON_ACCENT}">${rect(GLYPH.tab)}${rect(GLYPH.body)}</g>` +
    `<polygon points="${points}" fill="${ACCENT}"/></svg>\n`
  );
}

function manifest() {
  const icon = (src, size, type, purpose = "any") => ({ src, sizes: size, type, purpose });
  return {
    id: "/",
    name: "Platform",
    short_name: "Platform",
    description: "Je bestanden, foto's en documenten op je eigen server",
    lang: "nl",
    dir: "ltr",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: BG,
    theme_color: BG,
    icons: [
      icon("/icons/icon-192.png", "192x192", "image/png"),
      icon("/icons/icon-512.png", "512x512", "image/png"),
      icon("/icons/icon-maskable-512.png", "512x512", "image/png", "maskable"),
      icon("/icons/icon.svg", "any", "image/svg+xml"),
    ],
    shortcuts: [
      {
        name: "Gedeeld met mij",
        short_name: "Gedeeld",
        url: "/gedeeld",
        icons: [icon("/icons/icon-192.png", "192x192", "image/png")],
      },
      {
        name: "Zoeken",
        short_name: "Zoeken",
        url: "/zoeken",
        icons: [icon("/icons/icon-192.png", "192x192", "image/png")],
      },
    ],
  };
}

function main() {
  mkdirSync(join(PUBLIC, "icons"), { recursive: true });
  const rounded = { full: false, scale: 1 };
  const full = { full: true, scale: 1 };
  const maskable = { full: true, scale: 0.75 };
  const write = (name, data) => {
    writeFileSync(join(PUBLIC, name), data);
    console.log(`${name} (${data.length} B)`);
  };
  write("icons/icon.svg", svg(rounded));
  write("favicon.svg", svg(rounded));
  write("icons/icon-192.png", png(192, raster(192, rounded)));
  write("icons/icon-512.png", png(512, raster(512, rounded)));
  write("icons/icon-maskable-512.png", png(512, raster(512, maskable)));
  // iOS rondt zelf af en vult doorzichtig met zwart: tot de rand
  write("icons/apple-touch-icon.png", png(180, raster(180, full)));
  write(
    "favicon.ico",
    ico([16, 32, 48].map((size) => ({ size, data: png(size, raster(size, rounded)) }))),
  );
  write("manifest.webmanifest", `${JSON.stringify(manifest(), null, 2)}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
