// De sneltoetsen van de acties uit het register (actions.ts), voor useShortcuts. Bestanden en
// Zoeken gebruiken dezelfde: een actie werkt op target (de selectie, of de rij met de focus).
// Een actie die zichtbaar is maar niet kan, neemt de toets wel en zegt waarom niet.

import type { FileNode } from "../api";
import type { Shortcut } from "../ui/keys";
import { type ActionEnv, actionLabel, availability, type BrowserAction, browserActions } from "./actions";

export function actionShortcuts(
  target: readonly FileNode[],
  env: ActionEnv,
  run: (action: BrowserAction, nodes: readonly FileNode[]) => void,
  /** Staat de focus in de lijst? Enter (openen) werkt alleen daar. */
  inList: () => boolean,
): Shortcut[] {
  const out: Shortcut[] = [];
  for (const action of browserActions()) {
    if (!action.shortcut) continue;
    out.push({
      combo: typeof action.shortcut === "string" ? action.shortcut : [...action.shortcut],
      label: actionLabel(action, target),
      when: () => (action.id !== "open" || inList()) && availability(action, target, env).visible,
      run: () => run(action, target),
    });
  }
  return out;
}
