#!/usr/bin/env node
// Zoekt tekst in de UI die niet via t() loopt, met de parser van typescript (al aanwezig; TS 7
// heeft alleen de API in typescript/unstable/sync). Meldt:
//   - JSX-tekst met letters, en een letterlijke aria-label, title, placeholder of alt;
//   - t() buiten elke functie (op moduleniveau: dan is de taal of het gebied er nog niet);
//   - setAttribute("style", …) en <style> (CSP: geen inline stijlen).
// "// i18n-ignore" op dezelfde of de vorige regel slaat een melding over.
//
//   node scripts/check-i18n.mjs [--strict] [--files a.tsx b.ts …]
//
// Zonder --strict alleen een overzicht (exit 0); met --strict exit 1 bij een melding.
// src/i18n/** en tests worden overgeslagen.

import { existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SyntaxKind } from "typescript/unstable/ast";
import { skipTrivia } from "typescript/unstable/ast/scanner";
import { API } from "typescript/unstable/sync";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src");

const args = process.argv.slice(2);
const strict = args.includes("--strict");
const fileArgs = [];
for (let i = args.indexOf("--files") + 1; i > 0 && i < args.length; i++) {
  if (args[i].startsWith("--")) break;
  fileArgs.push(args[i]);
}

const ATTRS = new Set(["aria-label", "title", "placeholder", "alt"]);
const LETTER = /\p{L}/u;

/** Overslaan: de woordenboeken zelf en tests. */
function skipped(abs) {
  const rel = path.relative(src, abs).split(path.sep).join("/");
  return rel.startsWith("..") || rel.startsWith("i18n/") || /\.test\.tsx?$/.test(rel) || rel.endsWith(".d.ts");
}

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const abs = path.join(dir, name);
    if (statSync(abs).isDirectory()) walk(abs, out);
    else if (/\.tsx?$/.test(name)) out.push(abs);
  }
  return out;
}

function targets() {
  if (fileArgs.length === 0) return walk(src, []).filter((f) => !skipped(f));
  const out = [];
  for (const f of fileArgs) {
    const abs = path.resolve(process.cwd(), f);
    if (!existsSync(abs)) {
      console.error(`check-i18n: ${f} bestaat niet`);
      process.exitCode = 2;
      continue;
    }
    if (statSync(abs).isDirectory()) out.push(...walk(abs, []).filter((x) => !skipped(x)));
    else if (/\.tsx?$/.test(abs) && !skipped(abs)) out.push(abs);
  }
  return [...new Set(out)];
}

const FUNCTIONS = new Set([
  SyntaxKind.FunctionDeclaration,
  SyntaxKind.FunctionExpression,
  SyntaxKind.ArrowFunction,
  SyntaxKind.MethodDeclaration,
  SyntaxKind.Constructor,
  SyntaxKind.GetAccessor,
  SyntaxKind.SetAccessor,
]);

/** De tekst van een letterlijke string (ook `zonder ${}`), anders null. */
function literal(node) {
  if (!node) return null;
  if (node.kind === SyntaxKind.StringLiteral || node.kind === SyntaxKind.NoSubstitutionTemplateLiteral) return node.text;
  if (node.kind === SyntaxKind.TemplateExpression) {
    return [node.head.text, ...node.templateSpans.map((s) => s.literal.text)].join(" ");
  }
  if (node.kind === SyntaxKind.JsxExpression) return literal(node.expression);
  if (node.kind === SyntaxKind.ParenthesizedExpression) return literal(node.expression);
  return null;
}

function nameOf(node) {
  if (!node) return "";
  if (node.kind === SyntaxKind.Identifier) return node.text;
  if (node.kind === SyntaxKind.JsxNamespacedName) return `${node.namespace.text}:${node.name.text}`;
  return "";
}

function check(sf) {
  const findings = [];
  const lines = sf.text.split("\n");
  const ignored = (line) => /\/\/\s*i18n-ignore/.test(lines[line] ?? "") || /\/\/\s*i18n-ignore/.test(lines[line - 1] ?? "");
  const report = (node, message) => {
    const { line, character } = sf.getLineAndCharacterOfPosition(skipTrivia(sf.text, node.pos));
    if (ignored(line)) return;
    findings.push({ line: line + 1, col: character + 1, message });
  };

  const visit = (node, depth) => {
    const kind = node.kind;
    if (kind === SyntaxKind.JsxText) {
      const text = node.text.trim();
      if (LETTER.test(text)) report(node, `JSX-tekst zonder t(): "${short(text)}"`);
    } else if (kind === SyntaxKind.JsxAttribute) {
      const name = nameOf(node.name);
      const value = literal(node.initializer);
      if (ATTRS.has(name) && value !== null && LETTER.test(value)) {
        report(node, `${name} zonder t(): "${short(value)}"`);
      }
    } else if (kind === SyntaxKind.JsxOpeningElement || kind === SyntaxKind.JsxSelfClosingElement) {
      if (nameOf(node.tagName) === "style") report(node, "<style> in JSX: zet de stijl in een .css-bestand");
    } else if (kind === SyntaxKind.CallExpression) {
      const callee = node.expression;
      if (callee.kind === SyntaxKind.Identifier && callee.text === "t" && depth === 0) {
        report(node, "t() op moduleniveau: roep t() pas aan in een functie of component");
      }
      if (
        callee.kind === SyntaxKind.PropertyAccessExpression &&
        nameOf(callee.name) === "setAttribute" &&
        literal(node.arguments[0]) === "style"
      ) {
        report(node, 'setAttribute("style"): gebruik een klasse');
      }
    } else if (
      kind === SyntaxKind.StringLiteral ||
      kind === SyntaxKind.NoSubstitutionTemplateLiteral
    ) {
      if (/<style[\s>]/i.test(node.text)) report(node, "<style> in een string: zet de stijl in een .css-bestand");
    }
    const inner = FUNCTIONS.has(kind) ? depth + 1 : depth;
    node.forEachChild((child) => {
      visit(child, inner);
    });
  };
  visit(sf, 0);
  return findings;
}

function short(s) {
  const one = s.replace(/\s+/g, " ");
  return one.length > 60 ? `${one.slice(0, 57)}…` : one;
}

const files = targets();
const api = new API({ cwd: root });
let total = 0;
let withFindings = 0;
try {
  const configs = ["tsconfig.app.json", "tsconfig.worker.json"].map((c) => path.join(root, c)).filter(existsSync);
  const snapshot = api.updateSnapshot({ openProjects: configs });
  const programs = snapshot.getProjects().map((p) => p.program);
  for (const file of files) {
    let sf;
    for (const program of programs) {
      sf = program.getSourceFile(file);
      if (sf) break;
    }
    const rel = path.relative(process.cwd(), file);
    if (!sf) {
      console.error(`check-i18n: ${rel} hoort bij geen tsconfig`);
      continue;
    }
    const findings = check(sf);
    if (findings.length) withFindings++;
    for (const f of findings) console.log(`${rel}:${f.line}:${f.col}  ${f.message}`);
    total += findings.length;
  }
} finally {
  api.close();
}

console.log(`check-i18n: ${total} melding(en) in ${withFindings} van ${files.length} bestand(en)`);
if (strict && total > 0) process.exitCode = 1;
