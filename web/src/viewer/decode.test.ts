import { describe, expect, it } from "vitest";
import { bom, completeUtf8, decodeChunks, detectEncoding, isEncoding } from "./decode";

const utf8 = (s: string) => new TextEncoder().encode(s);

function cp1252(s: string): Uint8Array {
  // Alleen Latin-1 plus het euroteken: genoeg voor de test
  return Uint8Array.from([...s].map((c) => (c === "€" ? 0x80 : c.charCodeAt(0))));
}

function utf16(s: string, littleEndian: boolean, withBom = true): Uint8Array {
  const out: number[] = withBom ? (littleEndian ? [0xff, 0xfe] : [0xfe, 0xff]) : [];
  for (const c of s) {
    const code = c.charCodeAt(0);
    const pair = [code & 0xff, code >> 8];
    out.push(...(littleEndian ? pair : pair.reverse()));
  }
  return Uint8Array.from(out);
}

describe("codering raden", () => {
  it("een BOM zegt het zelf", () => {
    expect(bom(Uint8Array.from([0xef, 0xbb, 0xbf, 0x41]))).toEqual({ encoding: "utf-8", length: 3 });
    expect(detectEncoding(utf16("hé", true))).toBe("utf-16le");
    expect(detectEncoding(utf16("hé", false))).toBe("utf-16be");
    expect(bom(utf8("geen bom"))).toBeNull();
  });

  it("UTF-8 als het foutloos lukt, anders Windows-1252", () => {
    expect(detectEncoding(utf8("Café crème"))).toBe("utf-8");
    expect(detectEncoding(cp1252("Café crème"))).toBe("windows-1252");
    expect(detectEncoding(cp1252("€ 12"))).toBe("windows-1252");
    expect(detectEncoding(new Uint8Array(0))).toBe("utf-8");
  });

  it("een stuk dat midden in een teken eindigt, is nog UTF-8", () => {
    const bytes = utf8("abc€"); // € is drie bytes
    const cut = bytes.subarray(0, bytes.length - 1);
    expect(completeUtf8(cut)).toBe(3);
    expect(completeUtf8(bytes)).toBe(bytes.length);
    expect(detectEncoding(cut, false)).toBe("utf-8");
    expect(detectEncoding(cut, true)).toBe("windows-1252");
  });
});

describe("decodeChunks", () => {
  it("plakt stukken aan elkaar, ook midden in een teken", () => {
    const bytes = utf8("tweeën € drie");
    const chunks = [bytes.subarray(0, 5), bytes.subarray(5, 10), bytes.subarray(10)];
    expect(decodeChunks(chunks, "utf-8")).toBe("tweeën € drie");
  });

  it("zonder BOM, en in elke codering die de keuzelijst heeft", () => {
    expect(decodeChunks([Uint8Array.from([0xef, 0xbb, 0xbf, 0x61])], "utf-8")).toBe("a");
    expect(decodeChunks([cp1252("Café €")], "windows-1252")).toBe("Café €");
    expect(decodeChunks([utf16("hé", true)], "utf-16le")).toBe("hé");
    expect(decodeChunks([utf16("hé", false)], "utf-16be")).toBe("hé");
    // Verkeerd gekozen: geen fout, wel vervangingstekens
    expect(decodeChunks([cp1252("é")], "utf-8")).toBe("�");
  });

  it("isEncoding", () => {
    expect(isEncoding("windows-1252")).toBe(true);
    expect(isEncoding("latin1")).toBe(false);
    expect(isEncoding(undefined)).toBe(false);
  });
});
