import { describe, expect, it } from "vitest";
import { detectDelimiter, parseCsv } from "./csv";

describe("parseCsv (RFC 4180)", () => {
  it("velden, aanhalingstekens, regeleinden in een veld", () => {
    const text = 'naam,opmerking\r\n"Jansen, J.","zei ""hoi""\nen ging"\r\nPiet,\n';
    expect(parseCsv(text).rows).toEqual([
      ["naam", "opmerking"],
      ["Jansen, J.", 'zei "hoi"\nen ging'],
      ["Piet", ""],
    ]);
  });

  it("zonder regeleinde aan het eind, en lege velden", () => {
    expect(parseCsv("a,,c\n,,").rows).toEqual([
      ["a", "", "c"],
      ["", "", ""],
    ]);
    expect(parseCsv("").rows).toEqual([]);
  });

  it("een ander scheidingsteken", () => {
    expect(parseCsv("a;b\n1;2", ";").rows).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
    expect(detectDelimiter("a;b;c\n1;2;3")).toBe(";");
    expect(detectDelimiter('"x;y",b,c')).toBe(",");
    expect(detectDelimiter("a\tb")).toBe("\t");
    expect(detectDelimiter("a,b", "lijst.tsv")).toBe("\t");
  });

  it("houdt op bij de grens", () => {
    const text = Array.from({ length: 10 }, (_, i) => `${i},${i}`).join("\n");
    const table = parseCsv(text, ",", 3, 1);
    expect(table.rows).toEqual([["0"], ["1"], ["2"]]);
    expect(table.truncated).toBe(true);
    expect(parseCsv("a,b\n").truncated).toBe(false);
  });
});
