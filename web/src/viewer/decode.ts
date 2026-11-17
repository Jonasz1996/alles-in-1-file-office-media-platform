// Bytes naar tekst voor de tekstviewer. Een BOM zegt het zelf (UTF-8, UTF-16); anders UTF-8 als
// dat foutloos lukt, en anders Windows-1252 (wat oude Windows-bestanden meestal zijn). De
// keuzelijst in de viewer kan het altijd anders zetten.

export const ENCODINGS = ["utf-8", "windows-1252", "utf-16le", "utf-16be"] as const;
export type Encoding = (typeof ENCODINGS)[number];

export function isEncoding(value: unknown): value is Encoding {
  return typeof value === "string" && (ENCODINGS as readonly string[]).includes(value);
}

/** Welke codering een BOM aangeeft, en hoe lang die BOM is. */
export function bom(bytes: Uint8Array): { encoding: Encoding; length: number } | null {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return { encoding: "utf-8", length: 3 };
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) return { encoding: "utf-16le", length: 2 };
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) return { encoding: "utf-16be", length: 2 };
  return null;
}

/**
 * Hoeveel bytes vooraan een volledige UTF-8-reeks zijn: een stuk dat midden in een teken
 * eindigt (de grens van 512 KB), telt het halve teken aan het eind niet mee.
 */
export function completeUtf8(bytes: Uint8Array): number {
  const n = bytes.length;
  for (let back = 1; back <= Math.min(3, n); back++) {
    const b = bytes[n - back];
    if ((b & 0xc0) === 0x80) continue; // vervolgbyte: verder terug
    const need = b >= 0xf0 ? 4 : b >= 0xe0 ? 3 : b >= 0xc0 ? 2 : 1;
    return need > back ? n - back : n;
  }
  return n;
}

/** De codering raden. complete: dit zijn alle bytes (anders mag het eind een half teken zijn). */
export function detectEncoding(bytes: Uint8Array, complete = true): Encoding {
  const mark = bom(bytes);
  if (mark) return mark.encoding;
  const usable = complete ? bytes : bytes.subarray(0, completeUtf8(bytes));
  try {
    new TextDecoder("utf-8", { fatal: true }).decode(usable);
    return "utf-8";
  } catch {
    return "windows-1252";
  }
}

/** Windows-1252 voor 0x80..0x9F (de rest is gelijk aan Latin-1); 0 = niet in gebruik. */
const CP1252_HIGH = [
  0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017d, 0,
  0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178,
];

/**
 * Windows-1252 zelf: niet elke TextDecoder kent hem goed (Node zonder volledige ICU leest
 * Latin-1, en dan worden € en de aanhalingstekens stuurtekens). Eén byte is één teken; in
 * stukken van 8 KB naar een string, zodat er geen enorme argumentenlijst ontstaat.
 */
export function decodeCp1252(bytes: Uint8Array): string {
  let out = "";
  const codes = new Uint16Array(Math.min(bytes.length, 8192));
  for (let start = 0; start < bytes.length; start += codes.length) {
    const end = Math.min(bytes.length, start + codes.length);
    for (let i = start; i < end; i++) {
      const b = bytes[i];
      // Wat in 1252 geen teken heeft, blijft het stuurteken met dat nummer (zoals de browser)
      codes[i - start] = b >= 0x80 && b <= 0x9f ? CP1252_HIGH[b - 0x80] || b : b;
    }
    out += String.fromCharCode.apply(null, Array.from(codes.subarray(0, end - start)));
  }
  return out;
}

/**
 * Tekst uit stukken bytes (de eerste 512 KB, en wat "Meer laden" erbij haalt), in deze codering.
 * Ongeldige bytes worden het vervangingsteken; een BOM verdwijnt.
 */
export function decodeChunks(chunks: readonly Uint8Array[], encoding: Encoding, complete = true): string {
  if (encoding === "windows-1252") return chunks.map(decodeCp1252).join("");
  const decoder = new TextDecoder(encoding, { fatal: false, ignoreBOM: false });
  let text = "";
  chunks.forEach((chunk, i) => {
    text += decoder.decode(chunk, { stream: !complete || i < chunks.length - 1 });
  });
  return text;
}
