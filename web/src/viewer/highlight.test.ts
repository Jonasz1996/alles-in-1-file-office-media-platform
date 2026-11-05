import { describe, expect, it } from "vitest";
import { allLines, highlight as run, languageOf, type Line, lineTokens, MAX_HIGHLIGHT, plain } from "./highlight";

const highlight = (src: string, lang: string | null): Line[] => allLines(run(src, lang));

const text = (lines: Line[]) => lines.map((l) => l.map(([, s]) => s).join("")).join("\n");
const of = (lines: Line[], type: string) =>
  lines.flatMap((l) => l.filter(([t]) => t === type).map(([, s]) => s));

describe("highlight", () => {
  it("houdt de tekst precies zoals hij was", () => {
    const src = 'const a = "x"; // klaar\n/* twee\nregels */ let b = 12.5;\n';
    const lines = highlight(src, "c");
    expect(text(lines)).toBe(src);
    expect(lines).toHaveLength(4);
  });

  it("kent commentaar, strings, getallen en sleutelwoorden", () => {
    const lines = highlight('return "a\\"b" + 42 // x\n# geen commentaar', "c");
    expect(of(lines, "keyword")).toEqual(["return"]);
    expect(of(lines, "string")).toEqual(['"a\\"b"']);
    expect(of(lines, "number")).toEqual(["42"]);
    expect(of(lines, "comment")).toEqual(["// x"]);
    const py = highlight("def f():  # uitleg\n    return 'x'", "script");
    expect(of(py, "keyword")).toEqual(["def", "return"]);
    expect(of(py, "comment")).toEqual(["# uitleg"]);
    expect(of(highlight("SELECT * from t -- x", "sql"), "keyword")).toEqual(["SELECT", "from"]);
  });

  it("markup: tags, attributen en commentaar", () => {
    const lines = highlight('<a href="x">tekst</a><!-- c -->', "markup");
    expect(of(lines, "attr")).toEqual(["href"]);
    expect(of(lines, "string")).toEqual(['"x"']);
    expect(of(lines, "comment")).toEqual(["<!-- c -->"]);
    expect(text(lines)).toBe('<a href="x">tekst</a><!-- c -->');
  });

  it("de taal komt van de naam", () => {
    expect(languageOf("app.tsx")).toBe("c");
    expect(languageOf("script.PY")).toBe("script");
    expect(languageOf("pagina.html")).toBe("markup");
    expect(languageOf("Dockerfile")).toBe("script");
    expect(languageOf("notities.txt")).toBeNull();
    expect(highlight("let x", null)).toEqual([[["plain", "let x"]]]);
  });

  it("kwaadaardige invoer van 1 MB binnen 200 ms", () => {
    const cases: [string, string][] = [
      ["/*".repeat(512 * 1024), "c"],
      ['"\\'.repeat(340 * 1024), "c"],
      ["a".repeat(MAX_HIGHLIGHT), "c"],
      ["<a ".repeat(340 * 1024), "markup"],
      ["<!-".repeat(340 * 1024), "markup"],
      ["1.".repeat(512 * 1024), "script"],
      ["\n".repeat(MAX_HIGHLIGHT), "c"],
      ["if ".repeat(340 * 1024), "c"],
      ["a b ".repeat(256 * 1024), "c"],
    ];
    // Eén keer warm draaien: de eerste ronde meet anders vooral de JIT
    run("/* x */ if (a) { return 'b' }", "c");
    for (const [input, lang] of cases) {
      // De snelste van drie: een volle machine (andere tests tegelijk) meet anders vooral ruis
      let ms = Infinity;
      for (let round = 0; round < 3; round++) {
        const start = performance.now();
        const done = run(input.slice(0, MAX_HIGHLIGHT), lang);
        ms = Math.min(ms, performance.now() - start);
        expect(done.lineCount).toBeGreaterThan(0);
      }
      expect(ms, `${JSON.stringify(input.slice(0, 6))} (${lang})`).toBeLessThan(200);
    }
  }, 30_000);

  it("te groot: gewone tekst", () => {
    const big = "x".repeat(MAX_HIGHLIGHT + 1);
    expect(highlight(big, "c")).toEqual([[["plain", big]]]);
    expect(allLines(plain("a\n\nb"))).toEqual([[["plain", "a"]], [], [["plain", "b"]]]);
  });

  it("een stuk over meer regels wordt per regel geknipt, en een lange regel afgekort", () => {
    const h = run("x /* een\ntwee */ y", "c");
    expect(lineTokens(h, 0)).toEqual([["plain", "x "], ["comment", "/* een"]]);
    expect(lineTokens(h, 1)).toEqual([["comment", "twee */"], ["plain", " y"]]);
    expect(lineTokens(h, 0, 3)).toEqual([["plain", "x "], ["comment", "/"], ["plain", "…"]]);
    expect(lineTokens(h, 5)).toEqual([]);
  });
});
