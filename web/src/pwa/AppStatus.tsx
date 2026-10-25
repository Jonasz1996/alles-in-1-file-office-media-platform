// De offline-balk en de melding "Nieuwe versie beschikbaar — Vernieuwen" (P1). Staat altijd in
// de schil (App.tsx), ook op een publieke link. Het woordenboek pwa laadt pas als er iets te
// melden is; de service worker heeft het in de precache, dus offline is het er ook.

import { type JSX, useEffect, useSyncExternalStore } from "react";
import { type Area, t, useAreas } from "../i18n";
import { Icon } from "../ui/Icon";
import { useShortcuts } from "../ui/keys";
import { toast } from "../ui/toast";
import { applyUpdate, subscribeUpdate, updateReady } from "./register";
import "./shell.css";

const AREAS: readonly Area[] = ["pwa"];
const UPDATE_TOAST = "pwa-update";

function subscribeOnline(cb: () => void): () => void {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => {
    window.removeEventListener("online", cb);
    window.removeEventListener("offline", cb);
  };
}

const isOnline = () => navigator.onLine;
const alwaysOnline = () => true;
const noUpdate = () => false;

export function AppStatus(): JSX.Element | null {
  const online = useSyncExternalStore(subscribeOnline, isOnline, alwaysOnline);
  const update = useSyncExternalStore(subscribeUpdate, updateReady, noUpdate);
  if (online && !update) return null;
  return <Status online={online} update={update} />;
}

function Status({ online, update }: { online: boolean; update: boolean }): JSX.Element | null {
  const ready = useAreas(AREAS);
  // Shift+R laadt de nieuwe versie (de knop in de melding is ook met Tab te bereiken)
  useShortcuts(ready && update ? [{ combo: "shift+r", run: () => applyUpdate(), label: t("pwa.reloadShortcut") }] : []);
  useEffect(() => {
    if (!ready || !update) return;
    // Blijft staan tot hij gekozen of gesloten wordt
    return toast(t("pwa.updateReady"), {
      id: UPDATE_TOAST,
      timeoutMs: 0,
      action: { label: t("pwa.reload"), run: applyUpdate },
    });
  }, [ready, update]);
  if (!ready || online) return null;
  return (
    <div className="offline-banner" role="status">
      <Icon name="warning" size={16} />
      <strong>{t("pwa.offline")}</strong>
      <span className="offline-banner-text">{t("pwa.offlineText")}</span>
    </div>
  );
}
