// De store van één open map (folderStore.ts) als React-hook: maken, starten, live gebeurtenissen
// doorgeven en opruimen. Plus de weergavekeuze (lijst of raster), per apparaat bewaard.

import { useEffect, useState, useSyncExternalStore } from "react";
import { liveEvents } from "../events";
import { isShared } from "../sharing";
import { FolderStore, type FolderSnapshot } from "./folderStore";

/** Eén store per map; de component eromheen heeft een key per map. */
export function useFolderStore(folderId: string | null): { store: FolderStore; snap: FolderSnapshot } {
  const [store] = useState(() => new FolderStore(folderId));
  useEffect(() => {
    store.start();
    const stop = liveEvents.subscribe((event) =>
      store.onEvent(event, { shared: isShared(store.getSnapshot().folder) }),
    );
    return () => {
      stop();
      store.dispose();
    };
  }, [store]);
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return { store, snap };
}

export type View = "list" | "grid";

const VIEW_KEY = "platform-browser-view";

/** De laatst gekozen weergave op dit apparaat (zonder opslag: de lijst). */
export function loadView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === "grid" ? "grid" : "list";
  } catch {
    return "list";
  }
}

export function saveView(view: View): void {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    /* geen opslag (privévenster): dan alleen voor nu */
  }
}
