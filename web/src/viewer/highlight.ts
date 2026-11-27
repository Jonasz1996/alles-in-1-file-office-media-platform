// Syntaxkleuren voor de tekstviewer: een tokenizer die teken voor teken door de tekst loopt
// (een toestandsmachine), dus altijd lineair, ook bij kwaadaardige invoer. Geen reguliere
// expressies op de tekst zelf (die kunnen terugspringen). Ook gebruikt in highlight.worker.ts:
// geen DOM hier.
//
// Het resultaat is compact: getypte arrays met waar elke regel begint en waar elk gekleurd stuk
// begint en eindigt. Pas bij het tonen maakt lineTokens() de stukken van één regel (alleen voor
// de regels in beeld), zodat ook een bestand van 1 MB met een miljoen regels snel klaar is en
// zonder kopiëren uit de worker kan komen.

export type TokenType = "plain" | "comment" | "string" | "number" | "keyword" | "tag" | "attr";
/** Een regel om te tonen: stukken [soort, tekst] */
export type Line = [TokenType, string][];

/** Boven deze grootte (tekens) geen kleuren: dan gewone tekst. */
export const MAX_HIGHLIGHT = 1024 * 1024;
/** Vanaf deze grootte in een worker (de pagina blijft dan reageren). */
export const WORKER_FROM = 100 * 1024;

const TYPES: readonly TokenType[] = ["plain", "comment", "string", "number", "keyword", "tag", "attr"];
const COMMENT = 1;
const STRING = 2;
const NUMBER = 3;
const KEYWORD = 4;
const TAG = 5;
const ATTR = 6;

/** Gekleurde tekst: regels en stukken als posities in text. */
export interface Highlighted {
  text: string;
  /** Waar regel i begint; lineStarts[lineCount] = text.length + 1 */
  lineStarts: Uint32Array;
  lineCount: number;
  /** Gekleurde stukken k = 0..count-1: [starts[k], ends[k]) met soort types[k], op volgorde */
  starts: Uint32Array;
  ends: Uint32Array;
  types: Uint8Array;
  count: number;
}

interface Lang {
  line: readonly string[];
  block: readonly (readonly [string, string])[];
  quotes: string;
  /** Aanhalingstekens die over regels heen mogen lopen (`template`) */
  multiline: string;
  keywords: ReadonlySet<string>;
  caseless?: boolean;
  markup?: boolean;
}

const words = (s: string) => new Set(s.split(" "));

const C_LIKE = words(
  "abstract as async await break case catch class const continue default defer delete do else enum export extends false final finally fn for func function go if impl implements import in instanceof interface let match mod mut new nil null package private protected pub public return self static struct super switch this throw throws true try type typeof undefined use var void while yield int long char float double bool boolean string byte short unsigned signed sizeof namespace using override virtual readonly declare keyof chan select range map trait where loop crate dyn",
);
const SCRIPT = words(
  "and as assert async await break case class continue def del do done elif else end esac except fi finally for from function global if import in is lambda local nonlocal not or pass raise return then try until while with yield True False None true false nil echo export begin rescue ensure module require unless",
);
const SQL = words(
  "select from where and or not insert into values update set delete create table drop alter index view join left right inner outer full on group by order having limit offset as distinct union all null is in like between case when then else end primary key foreign references default exists begin commit rollback with returning",
);
/** Langere woorden zijn nooit een sleutelwoord: dan ook niet opzoeken */
const LONGEST_KEYWORD = 12;

const LANGS: Record<string, Lang> = {
  c: { line: ["//"], block: [["/*", "*/"]], quotes: "\"'`", multiline: "`", keywords: C_LIKE },
  css: { line: [], block: [["/*", "*/"]], quotes: "\"'", multiline: "", keywords: new Set() },
  script: { line: ["#"], block: [], quotes: "\"'", multiline: "", keywords: SCRIPT },
  sql: { line: ["--"], block: [["/*", "*/"]], quotes: "'\"", multiline: "", keywords: SQL, caseless: true },
  lua: { line: ["--"], block: [], quotes: "\"'", multiline: "", keywords: SCRIPT },
  markup: { line: [], block: [], quotes: "\"'", multiline: "\"'", keywords: new Set(), markup: true },
};

const EXT: Record<string, string> = {
  js: "c", mjs: "c", cjs: "c", ts: "c", tsx: "c", jsx: "c", java: "c", c: "c", h: "c", cc: "c",
  cpp: "c", hpp: "c", cs: "c", go: "c", rs: "c", kt: "c", swift: "c", php: "c", json: "c",
  scss: "css", css: "css", py: "script", rb: "script", sh: "script", bash: "script",
  zsh: "script", yml: "script", yaml: "script", toml: "script", ini: "script", conf: "script",
  cfg: "script", r: "script", pl: "script", dockerfile: "script", env: "script",
  sql: "sql", lua: "lua", html: "markup", htm: "markup", xml: "markup", svg: "markup", vue: "markup",
};

/** De taal bij een bestandsnaam, of null (dan gewone tekst). */
export function languageOf(name: string): string | null {
  const lower = name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  const ext = dot >= 0 ? lower.slice(dot + 1) : lower;
  return EXT[ext] ?? null;
}

function isIdentStart(c: number): boolean {
  return (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 95 || c === 36 || c > 127;
}

function isIdent(c: number): boolean {
  return isIdentStart(c) || (c >= 48 && c <= 57);
}

function isDigit(c: number): boolean {
  return c >= 48 && c <= 57;
}

function lineStartsOf(text: string): { lineStarts: Uint32Array; lineCount: number } {
  const n = text.length;
  let count = 1;
  for (let i = 0; i < n; i++) if (text.charCodeAt(i) === 10) count++;
  const lineStarts = new Uint32Array(count + 1);
  let line = 1;
  for (let i = 0; i < n; i++) if (text.charCodeAt(i) === 10) lineStarts[line++] = i + 1;
  lineStarts[count] = n + 1;
  return { lineStarts, lineCount: count };
}

/** Snel nagaan of een woord een sleutelwoord kan zijn: lengte en eerste letter. */
interface KeywordIndex {
  min: number;
  max: number;
  first: Uint8Array;
}

const keywordIndexes = new WeakMap<ReadonlySet<string>, KeywordIndex>();

function keywordIndex(lang: Lang): KeywordIndex {
  let index = keywordIndexes.get(lang.keywords);
  if (!index) {
    index = { min: Infinity, max: 0, first: new Uint8Array(128) };
    for (const word of lang.keywords) {
      index.min = Math.min(index.min, word.length);
      index.max = Math.min(LONGEST_KEYWORD, Math.max(index.max, word.length));
      const c = word.charCodeAt(0);
      if (c < 128) index.first[c] = 1;
      if (lang.caseless) {
        index.first[word.toUpperCase().charCodeAt(0)] = 1;
        index.first[word.toLowerCase().charCodeAt(0)] = 1;
      }
    }
    keywordIndexes.set(lang.keywords, index);
  }
  return index;
}

/** Verzamelt gekleurde stukken; een stuk dat aansluit op een van dezelfde soort, verlengt dat. */
class Spans {
  starts = new Uint32Array(1024);
  ends = new Uint32Array(1024);
  types = new Uint8Array(1024);
  count = 0;

  add(type: number, start: number, end: number): void {
    if (end <= start) return;
    const last = this.count - 1;
    if (last >= 0 && this.types[last] === type && this.ends[last] === start) {
      this.ends[last] = end;
      return;
    }
    if (this.count === this.starts.length) this.grow();
    this.starts[this.count] = start;
    this.ends[this.count] = end;
    this.types[this.count] = type;
    this.count++;
  }

  private grow(): void {
    const size = this.starts.length * 2;
    const starts = new Uint32Array(size);
    const ends = new Uint32Array(size);
    const types = new Uint8Array(size);
    starts.set(this.starts);
    ends.set(this.ends);
    types.set(this.types);
    this.starts = starts;
    this.ends = ends;
    this.types = types;
  }
}

function result(text: string, spans: Spans | null): Highlighted {
  const { lineStarts, lineCount } = lineStartsOf(text);
  const count = spans?.count ?? 0;
  return {
    text,
    lineStarts,
    lineCount,
    starts: spans ? spans.starts.slice(0, count) : new Uint32Array(0),
    ends: spans ? spans.ends.slice(0, count) : new Uint32Array(0),
    types: spans ? spans.types.slice(0, count) : new Uint8Array(0),
    count,
  };
}

/** Gewone tekst (te groot of geen taal): alleen de regels. */
export function plain(text: string): Highlighted {
  return result(text, null);
}

/** De tekst met kleuren. Onbekende taal of te groot: gewone tekst. */
export function highlight(text: string, language: string | null): Highlighted {
  const lang = language ? LANGS[language] : undefined;
  if (!lang || text.length > MAX_HIGHLIGHT) return plain(text);
  return result(text, lang.markup ? markup(text, lang) : code(text, lang));
}

function codes(s: string): number[] {
  return [...s].map((c) => c.charCodeAt(0));
}

function code(text: string, lang: Lang): Spans {
  const out = new Spans();
  const n = text.length;
  const quotes = codes(lang.quotes);
  const multiline = codes(lang.multiline);
  // Op het eerste teken vooraf filteren: startsWith alleen als dat teken klopt
  const lineFirst = lang.line.map((p) => p.charCodeAt(0));
  const blockFirst = lang.block.map(([open]) => open.charCodeAt(0));
  const kw = keywordIndex(lang);
  let i = 0;
  scan: while (i < n) {
    const c = text.charCodeAt(i);
    if (c === 32 || c === 10 || c === 9 || c === 13) {
      i++; // witruimte: niets te kleuren
      continue;
    }
    for (let p = 0; p < lineFirst.length; p++) {
      if (c === lineFirst[p] && text.startsWith(lang.line[p], i)) {
        let end = text.indexOf("\n", i);
        if (end < 0) end = n;
        out.add(COMMENT, i, end);
        i = end;
        continue scan;
      }
    }
    for (let p = 0; p < blockFirst.length; p++) {
      const [open, close] = lang.block[p];
      if (c === blockFirst[p] && text.startsWith(open, i)) {
        const at = text.indexOf(close, i + open.length);
        const end = at < 0 ? n : at + close.length;
        out.add(COMMENT, i, end);
        i = end;
        continue scan;
      }
    }
    if (quotes.includes(c)) {
      const multi = multiline.includes(c);
      let j = i + 1;
      while (j < n) {
        const d = text.charCodeAt(j);
        if (d === 92) {
          j += 2; // \ en het teken erna
          continue;
        }
        if (d === c) {
          j++;
          break;
        }
        if (d === 10 && !multi) break;
        j++;
      }
      j = Math.min(j, n);
      out.add(STRING, i, j);
      i = j;
      continue;
    }
    if (isDigit(c) && (i === 0 || !isIdent(text.charCodeAt(i - 1)))) {
      let j = i + 1;
      while (j < n) {
        const d = text.charCodeAt(j);
        if (isIdent(d) || d === 46) j++;
        else break;
      }
      out.add(NUMBER, i, j);
      i = j;
      continue;
    }
    if (isIdentStart(c)) {
      let j = i + 1;
      while (j < n && isIdent(text.charCodeAt(j))) j++;
      if (j - i >= kw.min && j - i <= kw.max && c < 128 && kw.first[c]) {
        const word = text.slice(i, j);
        if (lang.keywords.has(lang.caseless ? word.toLowerCase() : word)) out.add(KEYWORD, i, j);
      }
      i = j;
      continue;
    }
    i++;
  }
  return out;
}

function isNameChar(c: number): boolean {
  return isIdent(c) || c === 45 || c === 58 || c === 46; // - : .
}

function markup(text: string, lang: Lang): Spans {
  const out = new Spans();
  const n = text.length;
  const quotes = codes(lang.quotes);
  let i = 0;
  while (i < n) {
    const c = text.charCodeAt(i);
    if (c !== 60) {
      i++; // geen <: gewone tekst
      continue;
    }
    if (text.startsWith("<!--", i)) {
      const at = text.indexOf("-->", i + 4);
      const end = at < 0 ? n : at + 3;
      out.add(COMMENT, i, end);
      i = end;
      continue;
    }
    const next = text.charCodeAt(i + 1);
    if (!(isIdentStart(next) || next === 47 || next === 33 || next === 63)) {
      i++;
      continue;
    }
    // De naam van de tag: < plus / ! ? en dan de naam
    let j = i + 1;
    while (j < n) {
      const d = text.charCodeAt(j);
      if (d === 47 || d === 33 || d === 63) j++;
      else break;
    }
    while (j < n && isNameChar(text.charCodeAt(j))) j++;
    out.add(TAG, i, j);
    // Attributen tot > (of een nieuwe <: dan was deze tag niet af)
    while (j < n) {
      const d = text.charCodeAt(j);
      if (d === 62 || d === 60) break;
      if (quotes.includes(d)) {
        const at = text.indexOf(text[j], j + 1);
        const end = at < 0 ? n : at + 1;
        out.add(STRING, j, end);
        j = end;
      } else if (isIdentStart(d)) {
        let k = j + 1;
        while (k < n && (isIdent(text.charCodeAt(k)) || text.charCodeAt(k) === 45 || text.charCodeAt(k) === 58)) k++;
        out.add(ATTR, j, k);
        j = k;
      } else {
        if (d !== 32 && d !== 9 && d !== 10 && d !== 13) out.add(TAG, j, j + 1); // = en /
        j++;
      }
    }
    if (j < n && text.charCodeAt(j) === 62) {
      out.add(TAG, j, j + 1);
      j++;
    }
    i = j;
  }
  return out;
}

/** Het eerste gekleurde stuk dat na positie pos eindigt (binair zoeken). */
function firstSpanAfter(h: Highlighted, pos: number): number {
  let lo = 0;
  let hi = h.count;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (h.ends[mid] <= pos) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * De stukken van regel i (vanaf 0), om te tonen; hooguit maxChars tekens (een geminificeerd
 * bestand is één lange regel), met … als er meer is.
 */
export function lineTokens(h: Highlighted, i: number, maxChars = Infinity): Line {
  if (i < 0 || i >= h.lineCount) return [];
  const start = h.lineStarts[i];
  const full = h.lineStarts[i + 1] - 1; // zonder het regeleinde
  const end = Math.min(full, start + maxChars);
  const out: Line = [];
  let pos = start;
  for (let k = firstSpanAfter(h, start); k < h.count && h.starts[k] < end; k++) {
    const s = Math.max(start, h.starts[k]);
    const e = Math.min(end, h.ends[k]);
    if (s > pos) out.push(["plain", h.text.slice(pos, s)]);
    if (e > s) out.push([TYPES[h.types[k]], h.text.slice(s, e)]);
    pos = Math.max(pos, e);
  }
  if (end > pos) out.push(["plain", h.text.slice(pos, end)]);
  if (full > end) out.push(["plain", "…"]);
  return out;
}

/** Alle regels als stukken (voor tests en kleine teksten). */
export function allLines(h: Highlighted): Line[] {
  const out: Line[] = [];
  for (let i = 0; i < h.lineCount; i++) out.push(lineTokens(h, i));
  return out;
}
