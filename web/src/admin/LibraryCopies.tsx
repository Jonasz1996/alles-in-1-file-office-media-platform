// Kopieën in het cluster per bibliotheek (replicatie per bibliotheek, migratie 0019;
// docs/runbooks/replicatie.md): de keuze in de tabel Bibliotheken op /beheer/media en de uitleg
// eronder. "Standaard (N)" is de standaard van het cluster (PLATFORM_BLOB_DEFAULT_COPIES); 1, 2
// of 3 zet de inhoud in blobs-r1/ tot blobs-r3/ (app/blobsplit.py). Zonder PLATFORM_BLOB_SPLIT
// wordt de keuze bewaard maar doet ze nog niets; dat zeggen de uitleg en de melding.
//
// Opslaan gaat met de knop Opslaan (of Enter in de lijst), nooit bij elke keuze in de lijst: in
// Chrome en Firefox geeft een dichte <select> bij elke pijltjestoets een change, en wie van
// "Standaard (3)" naar 2 gaat, zou dan eerst 1 kopie opslaan (en een ronde wekken).

import { type JSX, useEffect, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { Button } from "../ui/Button";
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

/** De waarde van de lijst voor copies: null (de standaard) is "". */
export function copiesValue(copies: Copies | null | undefined): string {
  return copies == null ? "" : String(copies);
}

/** Wat Opslaan zou bewaren; undefined als de keuze in de lijst niet afwijkt van wat bewaard is. */
export function copiesToSave(saved: Copies | null | undefined, draft: string): Copies | null | undefined {
  return draft === copiesValue(saved) ? undefined : parseCopies(draft);
}

/** De melding na het opslaan. Zonder PLATFORM_BLOB_SPLIT is er geen ronde die iets verhuist. */
export function copiesSaved(name: string, copies: Copies | null, defaultCopies: number, split: boolean): string {
  if (!split) return t("admin.media.libraries.copiesSavedOff", { name });
  return copies === null
    ? t("admin.media.libraries.copiesSavedDefault", { name, n: defaultCopies })
    : t("admin.media.libraries.copiesSaved", { name, copies });
}

/** PATCH /api/v1/admin/media/libraries/{id}: ook de bibliotheek van een ander. */
export function setLibraryCopies(id: string, copies: Copies | null): Promise<AdminLibrary> {
  return api.request<AdminLibrary>("PATCH", `/api/v1/admin/media/libraries/${encodeURIComponent(id)}`, { copies });
}

/** De keuze van één bibliotheek: de lijst en Opslaan. Pas Opslaan (of Enter) roept onSave aan. */
export function CopiesSelect({
  lib,
  defaultCopies,
  disabled,
  onSave,
}: {
  lib: Pick<AdminLibrary, "id" | "name" | "copies">;
  defaultCopies: number;
  disabled?: boolean;
  onSave: (copies: Copies | null) => void;
}): JSX.Element {
  const [draft, setDraft] = useState(copiesValue(lib.copies));
  // Na het opslaan (of een ververs) toont de lijst weer wat bewaard is
  useEffect(() => setDraft(copiesValue(lib.copies)), [lib.copies]);
  const pending = copiesToSave(lib.copies, draft);
  const save = () => {
    if (pending !== undefined && !disabled) onSave(pending);
  };
  return (
    <span className="copies-pick">
      <select
        data-media="copies"
        aria-label={t("admin.media.libraries.copiesLabel", { name: lib.name })}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            save();
          }
        }}
      >
        {copiesChoices(defaultCopies).map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
      <Button
        data-media="copies-save"
        disabled={disabled || pending === undefined}
        aria-label={t("admin.media.libraries.copiesSaveLabel", { name: lib.name })}
        onClick={save}
      >
        {t("admin.media.libraries.copiesSave")}
      </Button>
    </span>
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
