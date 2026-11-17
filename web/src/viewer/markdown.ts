// Markdown als React-elementen (nooit innerHTML): koppen, alinea's, lijsten, citaten, code,
// tabellen, vet, cursief, code in de regel en links. HTML in de tekst blijft gewone tekst; een
// link werkt alleen naar http, https of mailto; een afbeelding wordt zijn alt-tekst. Alles
// lineair: wat geen sluitteken heeft, wordt één keer gezocht en daarna gewone tekst.

import { createElement as h, type ReactNode } from "react";

const MAX_DEPTH = 4;

/** Een veilig adres voor een link, of null. */
export function safeHref(raw: string): string | null {
  const url = raw.trim();
  if (!url || /[\s\u0000-\u001f]/.test(url)) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null; // relatief of kapot: geen link
  }
  return ["http:", "https:", "mailto:"].includes(parsed.protocol) ? parsed.href : null;
}

/** indexOf, maar wat vanaf een plek niet te vinden was, wordt verderop niet opnieuw gezocht. */
class Finder {
  private readonly failed = new Map<string, number>();

  constructor(private readonly text: string) {}

  find(needle: string, from: number): number {
    const failedAt = this.failed.get(needle);
    if (failedAt !== undefined && from >= failedAt) return -1;
    const at = this.text.indexOf(needle, from);
    if (at < 0) this.failed.set(needle, Math.min(from, failedAt ?? from));
    return at;
  }
}

function isWordChar(c: string | undefined): boolean {
  return !!c && /[\p{L}\p{N}]/u.test(c);
}

/** De tekst binnen een alinea of kop. */
export function inline(text: string, depth = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const finder = new Finder(text);
  let plain = "";
  let key = 0;
  const flush = () => {
    if (plain) out.push(plain);
    plain = "";
  };
  const push = (node: ReactNode) => {
    flush();
    out.push(node);
  };
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "\\" && i + 1 < text.length && /[\\`*_[\]()#+\-.!<>|~]/.test(text[i + 1])) {
      plain += text[i + 1];
      i += 2;
      continue;
    }
    if (c === "`") {
      const end = finder.find("`", i + 1);
      if (end > i + 1) {
        push(h("code", { key: key++ }, text.slice(i + 1, end)));
        i = end + 1;
        continue;
      }
    }
    if ((c === "*" || c === "_") && text[i + 1] === c && depth < MAX_DEPTH) {
      const end = finder.find(c + c, i + 2);
      if (end > i + 2) {
        push(h("strong", { key: key++ }, inline(text.slice(i + 2, end), depth + 1)));
        i = end + 2;
        continue;
      }
    }
    if ((c === "*" || c === "_") && depth < MAX_DEPTH && !(c === "_" && isWordChar(text[i - 1]))) {
      const end = finder.find(c, i + 1);
      if (end > i + 1 && text[i + 1] !== " ") {
        push(h("em", { key: key++ }, inline(text.slice(i + 1, end), depth + 1)));
        i = end + 1;
        continue;
      }
    }
    if (c === "!" && text[i + 1] === "[") {
      const mid = finder.find("](", i + 2);
      const end = mid > 0 ? finder.find(")", mid + 2) : -1;
      if (mid > 0 && end > 0) {
        const alt = text.slice(i + 2, mid);
        push(h("span", { key: key++, className: "md-image" }, alt));
        i = end + 1;
        continue;
      }
    }
    if (c === "[") {
      const mid = finder.find("](", i + 1);
      const end = mid > 0 ? finder.find(")", mid + 2) : -1;
      if (mid > 0 && end > 0) {
        const label = text.slice(i + 1, mid);
        const href = safeHref(text.slice(mid + 2, end).split(" ")[0]);
        const children = depth < MAX_DEPTH ? inline(label, depth + 1) : [label];
        push(
          href
            ? h("a", { key: key++, href, target: "_blank", rel: "noopener noreferrer nofollow" }, children)
            : h("span", { key: key++ }, children),
        );
        i = end + 1;
        continue;
      }
    }
    if (c === "<" && /^<(https?:|mailto:)/.test(text.slice(i, i + 9))) {
      const end = finder.find(">", i + 1);
      const href = end > 0 ? safeHref(text.slice(i + 1, end)) : null;
      if (href) {
        push(h("a", { key: key++, href, target: "_blank", rel: "noopener noreferrer nofollow" }, href));
        i = end + 1;
        continue;
      }
    }
    plain += c;
    i++;
  }
  flush();
  return out;
}

/** "## Titel ##" -> [2, "Titel"]; zonder reguliere expressie die kan terugspringen. */
function heading(line: string): [number, string] | null {
  let level = 0;
  while (level < line.length && line[level] === "#") level++;
  if (level < 1 || level > 6 || !/\s/.test(line[level] ?? "")) return null;
  let text = line.slice(level).trim();
  // Sluitende #'en alleen na een spatie ("## C#" blijft "C#")
  let end = text.length;
  while (end > 0 && text[end - 1] === "#") end--;
  if (end === 0 || text[end - 1] === " ") text = text.slice(0, end).trim();
  return [level, text];
}

/** "---", "* * *", "___" */
function isRule(line: string): boolean {
  const s = line.trim();
  const c = s[0];
  if (s.length < 3 || (c !== "-" && c !== "*" && c !== "_")) return false;
  let count = 0;
  for (const ch of s) {
    if (ch === c) count++;
    else if (ch !== " ") return false;
  }
  return count >= 3;
}
const BULLET = /^( *)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const FENCE = /^ {0,3}(```|~~~)/;

function isTableSeparator(line: string): boolean {
  return line.length < 2000 && line.includes("-") && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line);
}

function cells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

/** De blokken van een markdown-tekst als React-elementen. */
export function markdown(text: string, depth = 0): ReactNode[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let key = 0;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      out.push(h("pre", { key: key++, className: "md-code" }, h("code", null, body.join("\n"))));
      continue;
    }
    const head = heading(line);
    if (head) {
      out.push(h(`h${Math.min(6, head[0] + 1)}`, { key: key++ }, inline(head[1])));
      i++;
      continue;
    }
    if (isRule(line)) {
      out.push(h("hr", { key: key++ }));
      i++;
      continue;
    }
    if (line.trimStart().startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith(">")) {
        body.push(lines[i].trimStart().slice(1).replace(/^ /, ""));
        i++;
      }
      out.push(
        h("blockquote", { key: key++ }, depth < MAX_DEPTH ? markdown(body.join("\n"), depth + 1) : body.join("\n")),
      );
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      const ordered = /\d/.test(bullet[2]);
      const indent = bullet[1].length;
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const m = BULLET.exec(lines[i]);
        if (!m || m[1].length !== indent || /\d/.test(m[2]) !== ordered) break;
        const body = [m[3]];
        i++;
        // Wat dieper inspringt, hoort bij dit item (een lijst in een lijst)
        while (i < lines.length && lines[i].trim() && lines[i].length - lines[i].trimStart().length > indent) {
          body.push(lines[i].slice(Math.min(indent + 2, lines[i].length - lines[i].trimStart().length)));
          i++;
        }
        const [first, ...rest] = body;
        items.push(
          h(
            "li",
            { key: items.length },
            inline(first),
            rest.length && depth < MAX_DEPTH ? markdown(rest.join("\n"), depth + 1) : rest.join(" "),
          ),
        );
      }
      out.push(h(ordered ? "ol" : "ul", { key: key++ }, items));
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const titles = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push(
        h(
          "div",
          { key: key++, className: "table-wrap" },
          h(
            "table",
            null,
            h("thead", null, h("tr", null, titles.map((c, j) => h("th", { key: j }, inline(c))))),
            h(
              "tbody",
              null,
              rows.map((r, k) => h("tr", { key: k }, r.map((c, j) => h("td", { key: j }, inline(c))))),
            ),
          ),
        ),
      );
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !FENCE.test(lines[i]) &&
      !heading(lines[i]) &&
      !BULLET.test(lines[i]) &&
      !lines[i].trimStart().startsWith(">")
    ) {
      para.push(lines[i].trim());
      i++;
    }
    if (!para.length) {
      para.push(line);
      i++;
    }
    out.push(h("p", { key: key++ }, inline(para.join(" "))));
  }
  return out;
}
