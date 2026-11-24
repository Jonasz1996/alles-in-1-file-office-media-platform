// Kopieën in het cluster per bibliotheek (replicatie per bibliotheek, migratie 0019;
// docs/runbooks/replicatie.md): de keuze in de tabel Bibliotheken op /beheer/media en de uitleg
// eronder. "Standaard (N)" is de standaard van het cluster (PLATFORM_BLOB_DEFAULT_COPIES); 1, 2
// of 3 zet de inhoud in blobs-r1/ tot blobs-r3/ (app/blobsplit.py). Zonder PLATFORM_BLOB_SPLIT
// wordt de keuze bewaard maar doet ze nog niets; dat zegt de uitleg.

import type { JSX } from "react";
import { api } from "../api";
import { t } from "../i18n";
import type { Copies, MediaAdmin } from "../media/types";

type AdminLibrary = MediaAdmin["libraries"][number];

export const COPIES: readonly Copies[] = [1, 2, 3];

/** De keuzes van de lijst: eerst de standaard (waarde ""), dan 1, 2 en 3. */
export function copiesChoices(defaultCopies: number): { value: string; label: string }[] {
  return [
    { value: "", label: t("admin.media.libraries.copiesDefault", { n: defaultCopies }) },
    ...COPIES.map((n) => ({ value: String(n), label: String(n) })),
  ];
}

/** De waarde van de lijst terug naar copies: "" is null (de standaard). */
export function parseCopies(value: string): Copies | null {
  const n = Number(value);
  return value !== "" && (COPIES as readonly number[]).includes(n) ? (n as Copies) : null;
}

/** De melding na het opslaan. */
export function copiesSaved(name: string, copies: Copies | null, defaultCopies: number): string {
  return copies === null
    ? t("admin.media.libraries.copiesSavedDefault", { name, n: defaultCopies })
    : t("admin.media.libraries.copiesSaved", { name, copies });
}

/** PATCH /api/v1/admin/media/libraries/{id}: ook de bibliotheek van een ander. */
export function setLibraryCopies(id: string, copies: Copies | null): Promise<AdminLibrary> {
  return api.request<AdminLibrary>("PATCH", `/api/v1/admin/media/libraries/${encodeURIComponent(id)}`, { copies });
}

export function CopiesSelect({
  lib,
  defaultCopies,
  disabled,
  onChange,
}: {
  lib: Pick<AdminLibrary, "id" | "name" | "copies">;
  defaultCopies: number;
  disabled?: boolean;
  onChange: (copies: Copies | null) => void;
}): JSX.Element {
  return (
    <select
      data-media="copies"
      aria-label={t("admin.media.libraries.copiesLabel", { name: lib.name })}
      value={lib.copies == null ? "" : String(lib.copies)}
      disabled={disabled}
      onChange={(e) => onChange(parseCopies(e.currentTarget.value))}
    >
      {copiesChoices(defaultCopies).map((c) => (
        <option key={c.value} value={c.value}>
          {c.label}
        </option>
      ))}
    </select>
  );
}

/** De uitleg onder de tabel; zonder PLATFORM_BLOB_SPLIT ook dat het pas in het cluster werkt. */
export function CopiesNote({ split, defaultCopies }: { split: boolean; defaultCopies: number }): JSX.Element {
  return (
    <>
      <p className="muted" data-media="copies-help">
        {t("admin.media.libraries.copiesHelp", { n: defaultCopies })}
      </p>
      {!split && (
        <p className="muted" data-media="copies-off">
          {t("admin.media.libraries.copiesOff")}
        </p>
      )}
    </>
  );
}
