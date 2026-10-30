// CSV en TSV volgens RFC 4180: velden tussen dubbele aanhalingstekens mogen het scheidingsteken,
// regeleinden en "" (een aanhalingsteken) bevatten. Teken voor teken, dus lineair.

export interface Table {
  rows: string[][];
  /** Er waren meer rijen of kolommen dan getoond */
  truncated: boolean;
}

export const MAX_ROWS = 5000;
export const MAX_COLS = 200;

/** Het scheidingsteken: wat in de eerste regel (buiten aanhalingstekens) het vaakst staat. */
export function detectDelimiter(text: string, name = ""): string {
  if (/\.tsv$/i.test(name)) return "\t";
  const counts = new Map<string, number>([
    [",", 0],
    [";", 0],
    ["\t", 0],
    ["|", 0],
  ]);
  let quoted = false;
  for (let i = 0; i < text.length && i < 10_000; i++) {
    const c = text[i];
    if (c === '"') quoted = !quoted;
    else if (!quoted && (c === "\n" || c === "\r")) break;
    else if (!quoted && counts.has(c)) counts.set(c, (counts.get(c) ?? 0) + 1);
  }
  let best = ",";
  for (const [d, n] of counts) if (n > (counts.get(best) ?? 0)) best = d;
  return best;
}

export function parseCsv(text: string, delimiter = ",", maxRows = MAX_ROWS, maxCols = MAX_COLS): Table {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let truncated = false;
  let i = 0;
  const n = text.length;
  const endField = () => {
    if (row.length < maxCols) row.push(field);
    else truncated = true;
    field = "";
  };
  const endRow = () => {
    endField();
    rows.push(row);
    row = [];
  };
  while (i < n) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        quoted = false;
        i++;
        continue;
      }
      field += c;
      i++;
      continue;
    }
    if (c === '"' && field === "") {
      quoted = true;
      i++;
      continue;
    }
    if (c === delimiter) {
      endField();
      i++;
      continue;
    }
    if (c === "\r" || c === "\n") {
      endRow();
      i += c === "\r" && text[i + 1] === "\n" ? 2 : 1;
      if (rows.length >= maxRows) {
        if (i < n) truncated = true;
        return { rows, truncated };
      }
      continue;
    }
    field += c;
    i++;
  }
  if (field !== "" || row.length > 0) endRow();
  return { rows, truncated };
}
