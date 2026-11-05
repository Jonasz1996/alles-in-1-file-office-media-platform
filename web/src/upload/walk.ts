// Een losgelaten map (of een map uit "Map uploaden") uitlezen tot bestanden met hun pad.
// Statisch geïmporteerd door upload/index.ts: niets lazy op het droppad.
//
// Bij een drop zijn de items van dataTransfer alleen in de drop-handler zelf te lezen; daarom
// neemt uploadDropped daar synchroon de entries (webkitGetAsEntry) en de bestanden, en gaat
// collect() daarna asynchroon verder met die momentopname.

/** Wat collect van een entry nodig heeft (FileSystemEntry uit de browser, of een nep in tests). */
export interface EntryLike {
  readonly isFile: boolean;
  readonly isDirectory: boolean;
  readonly name: string;
  readonly fullPath: string;
  file?(success: (file: File) => void, error?: (e: unknown) => void): void;
  createReader?(): { readEntries(success: (entries: EntryLike[]) => void, error?: (e: unknown) => void): void };
}

export interface Collected {
  /** path: het relatieve pad inclusief de naam ("Vakantie/2024/a.jpg"; los bestand: "a.jpg") */
  files: { file: File; path: string }[];
  /** Alle mappen (relatieve paden), ook lege en alle tussenliggende, ondiep eerst */
  dirs: string[];
  /** Namen van "bestanden" die mappen bleken (een browser zonder entries-API) */
  rejected: string[];
}

/** Hoeveel bestanden tegelijk uit entries gelezen worden. */
const PARALLEL = 16;

function clean(path: string): string {
  return path.replace(/^\/+/, "").replace(/\/+$/, "");
}

function dirOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

/** Alle mappen boven deze paden plus de mappen zelf, ondiep eerst, elk één keer. */
export function allDirs(filePaths: Iterable<string>, dirs: Iterable<string> = []): string[] {
  const seen = new Set<string>();
  const add = (dir: string) => {
    let d = clean(dir);
    while (d && !seen.has(d)) {
      seen.add(d);
      d = dirOf(d);
    }
  };
  for (const p of filePaths) add(dirOf(clean(p)));
  for (const d of dirs) add(d);
  return [...seen].sort((a, b) => a.split("/").length - b.split("/").length || (a < b ? -1 : a > b ? 1 : 0));
}

function fileOf(entry: EntryLike): Promise<File> {
  return new Promise((resolve, reject) => {
    if (!entry.file) reject(new Error("geen bestand"));
    else entry.file(resolve, reject);
  });
}

/** Alle entries van een map: readEntries geeft er per keer hooguit een stuk of 100, tot []. */
export async function readAll(entry: EntryLike): Promise<EntryLike[]> {
  const reader = entry.createReader?.();
  if (!reader) return [];
  const out: EntryLike[] = [];
  for (;;) {
    const batch = await new Promise<EntryLike[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (batch.length === 0) return out;
    out.push(...batch);
  }
}

/**
 * Een "bestand" dat eigenlijk een map is: een browser zonder entries-API zet een losgelaten
 * map als bestand zonder type in dt.files (grootte 0 of een veelvoud van 4096), en lezen
 * mislukt dan.
 */
export async function isPseudoFolder(file: File): Promise<boolean> {
  if (file.type !== "" || file.size % 4096 !== 0) return false;
  try {
    await file.slice(0, 1).arrayBuffer();
    return false;
  } catch {
    return true;
  }
}

/** Een lijst bestanden die zelf hun relatieve pad dragen (input met webkitdirectory). */
export function fromInput(files: readonly File[]): Collected {
  const list = files.map((file) => ({ file, path: clean(file.webkitRelativePath || file.name) }));
  return { files: list, dirs: allDirs(list.map((f) => f.path)), rejected: [] };
}

/**
 * Leest de momentopname van een drop uit. entries[i] hoort bij files[i] (dezelfde volgorde als
 * dt.items en dt.files); zonder entry (null, of een browser zonder webkitGetAsEntry) geldt het
 * bestand zelf, behalve als het een map blijkt (rejected).
 */
export async function collect(entries: readonly (EntryLike | null)[], files: readonly File[]): Promise<Collected> {
  const out: Collected = { files: [], dirs: [], rejected: [] };
  const dirs: string[] = [];
  const fileEntries: EntryLike[] = [];
  const pending: EntryLike[] = [];
  const count = Math.max(entries.length, files.length);
  for (let i = 0; i < count; i++) {
    const entry = entries[i] ?? null;
    if (entry?.isDirectory) pending.push(entry);
    else if (entry?.isFile) fileEntries.push(entry);
    else if (files[i]) {
      const file = files[i];
      if (await isPseudoFolder(file)) out.rejected.push(file.name);
      else out.files.push({ file, path: file.name });
    }
  }
  // Breedte eerst door de mappen; elke map komt in dirs, ook als hij leeg is
  while (pending.length) {
    const dir = pending.shift() as EntryLike;
    dirs.push(clean(dir.fullPath || dir.name));
    for (const child of await readAll(dir)) {
      if (child.isDirectory) pending.push(child);
      else if (child.isFile) fileEntries.push(child);
    }
  }
  // Bestanden lezen, een paar tegelijk; een bestand dat niet te lezen is (weg, geen recht)
  // valt af
  for (let i = 0; i < fileEntries.length; i += PARALLEL) {
    const part = fileEntries.slice(i, i + PARALLEL);
    const read = await Promise.all(part.map((e) => fileOf(e).then((f) => f, () => null)));
    read.forEach((file, j) => {
      if (file) out.files.push({ file, path: clean(part[j].fullPath || part[j].name) });
    });
  }
  out.dirs = allDirs(
    out.files.map((f) => f.path),
    dirs,
  );
  return out;
}
