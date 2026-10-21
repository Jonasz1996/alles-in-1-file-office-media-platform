// De lijst met sneltoetsen die nu werken (toets ?, of via het accountmenu).

import { type JSX, useState, useSyncExternalStore } from "react";
import { type MessageKey, t } from "../i18n";
import { Dialog } from "./Dialog";
import { activeShortcuts, formatCombo, useShortcuts } from "./keys";

const listeners = new Set<() => void>();
let open = false;

function setOpen(v: boolean): void {
  open = v;
  for (const l of listeners) l();
}

/** Opent de hulp (bijv. vanuit het accountmenu). */
export function openShortcutHelp(): void {
  setOpen(true);
}

const SCOPES: Record<string, MessageKey> = {
  global: "shell.shortcuts.scopes.global",
  dialog: "shell.shortcuts.scopes.dialog",
  menu: "shell.shortcuts.scopes.menu",
  browser: "shell.shortcuts.scopes.browser",
  viewer: "shell.shortcuts.scopes.viewer",
  photos: "shell.shortcuts.scopes.photos",
  trash: "shell.shortcuts.scopes.trash",
};

function scopeLabel(scope: string): string {
  return t(SCOPES[scope] ?? "shell.shortcuts.scopes.page");
}

/** Eén keer in de app: ? opent de hulp. */
export function ShortcutHelp(): JSX.Element | null {
  const isOpen = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
    () => open,
    () => open,
  );
  useShortcuts([{ combo: "?", label: t("shell.shortcuts.open"), run: () => setOpen(true) }]);
  if (!isOpen) return null;
  return <ShortcutList onClose={() => setOpen(false)} />;
}

function ShortcutList({ onClose }: { onClose(): void }) {
  // De lijst van vóór het openen: de dialoog zelf zet een scope bovenop die de rest tegenhoudt.
  // Zonder "menu": vanuit het accountmenu staat dat nog even op de stapel (het sluit in een
  // effect na deze render), en het zou alles eronder tegenhouden
  const [list] = useState(() => activeShortcuts({ ignore: ["menu"] }));
  const groups = new Map<string, { combo: string; label: string }[]>();
  for (const s of list) {
    const group = groups.get(s.scope) ?? [];
    group.push(s);
    groups.set(s.scope, group);
  }
  return (
    <Dialog title={t("shell.shortcuts.title")} onClose={onClose}>
      {list.length === 0 && <p className="muted">{t("shell.shortcuts.empty")}</p>}
      {[...groups].map(([scope, items]) => (
        <section key={scope} className="shortcut-group">
          <h3>{scopeLabel(scope)}</h3>
          <dl className="shortcut-list">
            {items.map((s) => (
              <div key={`${s.combo}-${s.label}`}>
                <dt>
                  <kbd>{formatCombo(s.combo)}</kbd>
                </dt>
                <dd>{s.label}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </Dialog>
  );
}
