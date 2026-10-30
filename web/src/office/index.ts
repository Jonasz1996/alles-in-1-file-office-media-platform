// De ingang van office voor de rest van de web-app (fase 4, frozen na O0): de typen en aanroepen
// van §2, de status als React-hook, en het adres van de editor. De schil (App.tsx, routes.tsx)
// importeert dit nooit: dan zou het in de eerste lading komen (Keuze 33).

import { useEffect, useSyncExternalStore } from "react";
import { officeStatus } from "./api";
import type { OfficeStatus } from "./types";

export * from "./api";
export * from "./types";

/** De pagina van de editor (routes.tsx: /bewerken/:id, Keuze 32). */
export function editorPath(id: string): string {
  return `/bewerken/${encodeURIComponent(id)}`;
}

/**
 * De status van office (null: onbekend, of office is er niet). Vraagt hem bij het tonen als hij
 * oud is. active false: niets vragen en null geven (bijv. de viewer via een publieke link).
 */
export function useOfficeStatus(active = true): OfficeStatus | null {
  const status = useSyncExternalStore(officeStatus.subscribe, officeStatus.peek, () => null);
  useEffect(() => {
    if (active && officeStatus.stale()) void officeStatus.load();
  }, [active]);
  return active ? status : null;
}
