import { useSyncExternalStore } from "react";
import type { FileNode } from "./api";
import { type ActionEnv, actionMenuItems } from "./browser/actions";
import { clipboard } from "./browser/clipboard";
import { useBrowserUi } from "./browser/useBrowserUi";
import { isGuest, useCurrentUser } from "./currentUser";
import { t } from "./i18n";
import { MenuButton } from "./ui/Menu";

/**
 * Het menu ⋯ bij één map of bestand, met de acties uit het register (browser/actions.ts):
 * openen, downloaden, hernoemen, knippen en kopiëren, verplaatsen, tags, delen, versies en naar
 * de prullenbak. Wat niet mag, staat er uitgeschakeld in met de reden. Na een wijziging roept hij
 * onChanged aan (de ouder laadt opnieuw); een fout gaat naar onError of een melding.
 */
export function NodeActions({
  node,
  folderId,
  onChanged,
  onError,
  shareRoot: _shareRoot = false,
}: {
  node: FileNode;
  /** De map die nu open staat; daar begint het kiezen van een doel. */
  folderId: string | null;
  onChanged: () => void;
  onError: (message: string) => void;
  /** Compat: de gedeelde map (of het gedeelde bestand) zelf herkent het register zelf (geen ouder). */
  shareRoot?: boolean;
}) {
  const user = useCurrentUser();
  const clip = useSyncExternalStore(clipboard.subscribe, clipboard.getSnapshot, clipboard.getSnapshot);
  // De map die open staat kennen we hier alleen bij id: plakken gaat via Bestanden zelf
  const folder: FileNode | null = null;
  const { ui, dialogs } = useBrowserUi({
    folder,
    me: user?.id ?? null,
    startId: folderId,
    onError,
    onUpsert: onChanged,
    onRemove: onChanged,
    onRefresh: onChanged,
  });
  const env: ActionEnv = { folder, clip, me: user?.id ?? null, guest: isGuest(user) };
  return (
    <>
      <MenuButton
        icon="more"
        label={t("browser.row.actions", { name: node.name })}
        items={() => actionMenuItems([node], env, (action) => void action.run([node], { ...env, ui }))}
      />
      {dialogs}
    </>
  );
}
