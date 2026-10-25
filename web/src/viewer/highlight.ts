// Syntaxkleuren voor de tekstviewer: een tokenizer die teken voor teken door de tekst loopt
// (een toestandsmachine), dus altijd lineair, ook bij kwaadaardige invoer. Geen reguliere
// expressies op de tekst zelf (die kunnen terugspringen). Ook gebruikt in highlight.worker.ts:
// geen DOM hier.

export type TokenType = "plain" | "comment" | "string" | "number" | "keyword" | "tag" | "attr";
/** Een regel: stukken [soort, tekst] */
export type Line = [TokenType, string][];

/** Boven deze grootte (tekens) geen kleuren: dan gewone tekst. */
export const MAX_HIGHLIGHT = 1024 * 1024;
/** Vanaf deze grootte in een worker (de pagina blijft dan reageren). */
export const WORKER_FROM = 100 * 1024;

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

/** Stukken naar regels: een stuk met regeleinden wordt over regels verdeeld. */
class Lines {
  readonly lines: Line[] = [[]];

  push(type: TokenType, text: string): void {
    let start = 0;
    for (;;) {
      const nl = text.indexOf("\n", start);
      const piece = nl < 0 ? text.slice(start) : text.slice(start, nl);
      if (piece) {
        const line = this.lines[this.lines.length - 1];
        const last = line[line.length - 1];
        if (last && last[0] === type) last[1] += piece;
        else line.push([type, piece]);
      }
      if (nl < 0) return;
      this.lines.push([]);
      start = nl + 1;
    }
  }
}

/** Gewone tekst als regels (te groot of geen taal). */
export function plainLines(text: string): Line[] {
  return text.split("\n").map((l) => (l ? [["plain", l]] : []));
}

/** De tekst in gekleurde regels. Onbekende taal of te groot: gewone tekst. */
export function highlight(text: string, language: string | null): Line[] {
  const lang = language ? LANGS[language] : undefined;
  if (!lang || text.length > MAX_HIGHLIGHT) return plainLines(text);
  return lang.markup ? markup(text, lang) : code(text, lang);
}

function code(text: string, lang: Lang): Line[] {
  const out = new Lines();
  const n = text.length;
  let i = 0;
  let plainStart = 0;
  const flush = (end: number) => {
    if (end > plainStart) out.push("plain", text.slice(plainStart, end));
  };
  outer: while (i < n) {
    const c = text.charCodeAt(i);
    for (const prefix of lang.line) {
      if (text.startsWith(prefix, i)) {
        flush(i);
        let end = text.indexOf("\n", i);
        if (end < 0) end = n;
        out.push("comment", text.slice(i, end));
        i = plainStart = end;
        continue outer;
      }
    }
    for (const [open, close] of lang.block) {
      if (text.startsWith(open, i)) {
        flush(i);
        const at = text.indexOf(close, i + open.length);
        const end = at < 0 ? n : at + close.length;
        out.push("comment", text.slice(i, end));
        i = plainStart = end;
        continue outer;
      }
    }
    const ch = text[i];
    if (lang.quotes.includes(ch)) {
      flush(i);
      const multi = lang.multiline.includes(ch);
      let j = i + 1;
      while (j < n) {
        const d = text[j];
        if (d === "\\") {
          j += 2;
          continue;
        }
        if (d === ch) {
          j++;
          break;
        }
        if (d === "\n" && !multi) break;
        j++;
      }
      j = Math.min(j, n);
      out.push("string", text.slice(i, j));
      i = plainStart = j;
      continue;
    }
    if (isDigit(c) && (i === 0 || !isIdent(text.charCodeAt(i - 1)))) {
      flush(i);
      let j = i + 1;
      while (j < n) {
        const d = text.charCodeAt(j);
        if (isIdent(d) || d === 46) j++;
        else break;
      }
      out.push("number", text.slice(i, j));
      i = plainStart = j;
      continue;
    }
    if (isIdentStart(c)) {
      let j = i + 1;
      while (j < n && isIdent(text.charCodeAt(j))) j++;
      const word = text.slice(i, j);
      if (lang.keywords.has(lang.caseless ? word.toLowerCase() : word)) {
        flush(i);
        out.push("keyword", word);
        plainStart = j;
      }
      i = j;
      continue;
    }
    i++;
  }
  flush(n);
  return out.lines;
}

function markup(text: string, lang: Lang): Line[] {
  const out = new Lines();
  const n = text.length;
  let i = 0;
  let plainStart = 0;
  const flush = (end: number) => {
    if (end > plainStart) out.push("plain", text.slice(plainStart, end));
  };
  while (i < n) {
    if (text.startsWith("<!--", i)) {
      flush(i);
      const at = text.indexOf("-->", i + 4);
      const end = at < 0 ? n : at + 3;
      out.push("comment", text.slice(i, end));
      i = plainStart = end;
      continue;
    }
    const next = text.charCodeAt(i + 1);
    if (text[i] === "<" && (isIdentStart(next) || next === 47 || next === 33 || next === 63)) {
      flush(i);
      // De naam van de tag
      let j = i + 1;
      while (j < n && (text[j] === "/" || text[j] === "!" || text[j] === "?")) j++;
      while (j < n && (isIdent(text.charCodeAt(j)) || text[j] === "-" || text[j] === ":" || text[j] === ".")) j++;
      out.push("tag", text.slice(i, j));
      // Attributen tot >
      while (j < n && text[j] !== ">" && text[j] !== "<") {
        const ch = text[j];
        if (lang.quotes.includes(ch)) {
          const at = text.indexOf(ch, j + 1);
          const end = at < 0 ? n : at + 1;
          out.push("string", text.slice(j, end));
          j = end;
        } else if (isIdentStart(text.charCodeAt(j))) {
          let k = j + 1;
          while (k < n && (isIdent(text.charCodeAt(k)) || text[k] === "-" || text[k] === ":")) k++;
          out.push("attr", text.slice(j, k));
          j = k;
        } else {
          out.push("tag", ch);
          j++;
        }
      }
      if (j < n && text[j] === ">") {
        out.push("tag", ">");
        j++;
      }
      i = plainStart = j;
      continue;
    }
    i++;
  }
  flush(n);
  return out.lines;
}
