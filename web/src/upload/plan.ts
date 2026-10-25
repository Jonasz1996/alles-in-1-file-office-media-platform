// Wat er met dubbele bestanden gebeurt: één keuze per batch (Vervangen, Beide houden of
// Overslaan), vooraf, voordat er een upload bestaat. Puur (los te testen).

/** De keuze in de dialoog: vervangen (de vorige wordt een versie), beide houden, overslaan. */
export type ConflictChoice = "replace" | "keep" | "skip";

export interface PlanFile {
  file: File;
  /** id van de map waar hij in komt */
  parent: string;
}

/** Een bestand dat geüpload wordt, met wat de server moet doen als de naam toch bezet is. */
export type Planned<T extends PlanFile> = T & { conflict: "rename" | "replace" };

/** Naam in kleine letters, zoals de server vergelijkt (sibling(): lower(name)). */
export function nameKey(name: string): string {
  return name.toLowerCase();
}

/** Welke bestanden (indexen) botsen met wat er al in hun map staat? takenByFolder: per map de
 * bezette namen in kleine letters (nameKey). */
export function findConflicts(
  files: readonly PlanFile[],
  takenByFolder: ReadonlyMap<string, ReadonlySet<string>>,
): number[] {
  const out: number[] = [];
  files.forEach((f, i) => {
    if (takenByFolder.get(f.parent)?.has(nameKey(f.file.name))) out.push(i);
  });
  return out;
}

/**
 * Wat er geüpload wordt. Zonder botsing met conflict "rename": komt er intussen toch iets met
 * die naam, dan nooit overschrijven. Met een botsing de keuze: replace, rename (beide houden) of
 * overslaan. choice: één keuze voor allemaal, of per bestand (index in files).
 */
export function planConflicts<T extends PlanFile>(
  files: readonly T[],
  takenByFolder: ReadonlyMap<string, ReadonlySet<string>>,
  choice: ConflictChoice | ((file: T, index: number) => ConflictChoice),
): { create: Planned<T>[]; skipped: T[] } {
  const create: Planned<T>[] = [];
  const skipped: T[] = [];
  files.forEach((f, i) => {
    const clash = takenByFolder.get(f.parent)?.has(nameKey(f.file.name)) ?? false;
    if (!clash) {
      create.push({ ...f, conflict: "rename" });
      return;
    }
    const c = typeof choice === "function" ? choice(f, i) : choice;
    if (c === "skip") skipped.push(f);
    else create.push({ ...f, conflict: c === "replace" ? "replace" : "rename" });
  });
  return { create, skipped };
}
