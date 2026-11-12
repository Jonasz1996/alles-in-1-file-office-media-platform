import type { JSX } from "react";
import { t } from "../i18n";

/** Een draaiertje. Met inline (in een knop) zonder eigen tekst voor schermlezers. */
export function Spinner({ label, inline = false }: { label?: string; inline?: boolean }): JSX.Element {
  if (inline) return <span className="spinner" aria-hidden="true" />;
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="sr-only">{label ?? t("common.loading")}</span>
    </span>
  );
}

/** Grijze blokken op de plek van inhoud die nog laadt (aria-hidden). */
export function Skeleton({ width, height, lines = 1 }: { width?: string; height?: string; lines?: number }): JSX.Element {
  return (
    <span className="skeleton-group" aria-hidden="true">
      {Array.from({ length: Math.max(1, lines) }, (_, i) => (
        <span key={i} className="skeleton" style={{ width, height }} />
      ))}
    </span>
  );
}
