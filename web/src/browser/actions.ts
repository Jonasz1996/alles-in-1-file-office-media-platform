// Het register van acties op een selectie (de selectiebalk, het contextmenu, het menu ⋯ en de
// sneltoetsen gebruiken allemaal dit). Een actie zegt zelf of hij zichtbaar is en kan (met de
// reden als het niet kan), voor de hele selectie: de rechten van nodeActions() van elke node
// samen. Groepen: openen, bewerken, delen, info, omzetten (gereserveerd, leeg: fase 10) en
// prullenbak. registerAction() voegt er een toe (fase 10). Teksten gaan pas bij het tonen door
// t(): nooit op moduleniveau.

import type { FileNode } from "../api";
import { type MessageKey, t } from "../i18n";
import { canWrite, isShared, MAX_MULTI_SHARE, nodeActions, permissionOf } from "../sharing";
import type { IconName } from "../ui/Icon";
import type { Combo } from "../ui/keys";
import type { MenuItem } from "../ui/menu";
import type { Clip } from "./clipboard";

export type ActionGroup = "open" | "edit" | "share" | "info" | "convert" | "trash";

/** De volgorde van de groepen in een menu; "convert" (omzetten) is voor fase 10. */
export const GROUPS: readonly ActionGroup[] = ["open", "edit", "share", "info", "convert", "trash"];

export interface Availability {
  visible: boolean;
  enabled: boolean;
  /** Waarom het niet kan (bij enabled false) */
  reason?: string;
}

/** Wat een actie over de omgeving moet weten. */
export interface ActionEnv {
  /** De map die open staat */
  folder: FileNode | null;
  clip: Clip | null;
  /** De ingelogde gebruiker */
  me: string | null;
  guest: boolean;
}

/** Wat de pagina doet als een actie loopt (Files.tsx vult dit in). */
export interface BrowserUi {
  open(node: FileNode): void;
  download(nodes: readonly FileNode[]): void;
  rename(node: FileNode): void;
  cut(nodes: readonly FileNode[]): void;
  copy(nodes: readonly FileNode[]): void;
  paste(): void;
  move(nodes: readonly FileNode[]): void;
  copyTo(nodes: readonly FileNode[]): void;
  tags(node: FileNode): void;
  share(nodes: readonly FileNode[]): void;
  versions(node: FileNode): void;
  addToAlbum(nodes: readonly FileNode[]): void;
  trash(nodes: readonly FileNode[]): void;
}

export interface ActionContext extends ActionEnv {
  ui: BrowserUi;
}

export interface BrowserAction {
  id: string;
  /** De tekst (een sleutel; t() pas bij het tonen), eventueel per selectie */
  labelKey: MessageKey | ((sel: readonly FileNode[]) => MessageKey);
  icon?: IconName;
  group: ActionGroup;
  shortcut?: Combo | readonly Combo[];
  danger?: boolean;
  /** Werkt ook zonder selectie (op de map zelf), zoals plakken */
  withoutSelection?: boolean;
  when(sel: readonly FileNode[], folder: FileNode | null, env: ActionEnv): Availability;
  run(sel: readonly FileNode[], ctx: ActionContext): void | Promise<void>;
}

/** Hooguit zoveel items in één keer delen (ShareDialog nodes=): dezelfde grens als de dialoog. */
export const MAX_SHARE = MAX_MULTI_SHARE;

const HIDDEN: Availability = { visible: false, enabled: false };
const OK: Availability = { visible: true, enabled: true };

function no(reason: string): Availability {
  return { visible: true, enabled: false, reason };
}

/** De gedeelde map (of het gedeelde bestand) zelf: de server geeft dan geen ouder. */
export function isShareRoot(node: FileNode): boolean {
  return isShared(node) && node.parent_id === null;
}

type Right = keyof ReturnType<typeof nodeActions>;

/** Mag het voor elke node in de selectie? */
export function allowed(sel: readonly FileNode[], right: Right): boolean {
  return sel.length > 0 && sel.every((n) => n.kind !== "album" && nodeActions(n, isShareRoot(n))[right]);
}

/** Waarom wijzigen (hernoemen, verplaatsen, weggooien) niet kan voor deze selectie. */
function changeReason(sel: readonly FileNode[]): string {
  if (sel.some((n) => isShareRoot(n) && permissionOf(n) === "write")) return t("browser.reasons.shareRoot");
  return t("browser.reasons.readOnly");
}

function single(sel: readonly FileNode[]): boolean {
  return sel.length === 1;
}

function isMedia(node: FileNode): boolean {
  const mime = node.mime ?? "";
  return node.kind === "file" && (mime.startsWith("image/") || mime.startsWith("video/"));
}

const BUILT_IN: BrowserAction[] = [
  {
    id: "open",
    labelKey: "browser.actions.open",
    group: "open",
    shortcut: "enter",
    when: (sel) => (single(sel) ? OK : HIDDEN),
    run: (sel, ctx) => ctx.ui.open(sel[0]),
  },
  {
    id: "download",
    labelKey: (sel) => (single(sel) && sel[0].kind === "file" ? "browser.actions.download" : "browser.actions.downloadZip"),
    icon: "download",
    group: "open",
    shortcut: "d",
    when: (sel) => (sel.length === 0 ? HIDDEN : sel.some((n) => n.kind === "album") ? HIDDEN : OK),
    run: (sel, ctx) => ctx.ui.download(sel),
  },
  {
    id: "rename",
    labelKey: "browser.actions.rename",
    icon: "rename",
    group: "edit",
    shortcut: "f2",
    when: (sel) => (!single(sel) ? HIDDEN : allowed(sel, "rename") ? OK : no(changeReason(sel))),
    run: (sel, ctx) => ctx.ui.rename(sel[0]),
  },
  {
    id: "cut",
    labelKey: "browser.actions.cut",
    group: "edit",
    shortcut: "mod+x",
    when: (sel) => (sel.length === 0 ? HIDDEN : allowed(sel, "move") ? OK : no(changeReason(sel))),
    run: (sel, ctx) => ctx.ui.cut(sel),
  },
  {
    id: "copy",
    labelKey: "browser.actions.copy",
    icon: "copy",
    group: "edit",
    shortcut: "mod+c",
    when: (sel) => (sel.length === 0 || sel.some((n) => n.kind === "album") ? HIDDEN : OK),
    run: (sel, ctx) => ctx.ui.copy(sel),
  },
  {
    id: "paste",
    labelKey: "browser.actions.paste",
    group: "edit",
    shortcut: "mod+v",
    withoutSelection: true,
    when: (_sel, folder, env) => {
      if (!env.clip || !folder) return HIDDEN;
      return canWrite(permissionOf(folder)) ? OK : no(t("browser.reasons.readOnly"));
    },
    run: (_sel, ctx) => ctx.ui.paste(),
  },
  {
    id: "move",
    labelKey: "browser.actions.move",
    icon: "move",
    group: "edit",
    shortcut: "m",
    when: (sel) => (sel.length === 0 ? HIDDEN : allowed(sel, "move") ? OK : no(changeReason(sel))),
    run: (sel, ctx) => ctx.ui.move(sel),
  },
  {
    id: "copyTo",
    labelKey: "browser.actions.copyTo",
    icon: "copy",
    group: "edit",
    shortcut: "shift+m",
    when: (sel) => (sel.length === 0 || sel.some((n) => n.kind === "album") ? HIDDEN : OK),
    run: (sel, ctx) => ctx.ui.copyTo(sel),
  },
  {
    id: "tags",
    labelKey: "browser.actions.tags",
    icon: "tag",
    group: "edit",
    shortcut: "t",
    when: (sel) => (single(sel) && sel[0].kind !== "album" ? OK : HIDDEN),
    run: (sel, ctx) => ctx.ui.tags(sel[0]),
  },
  {
    id: "share",
    labelKey: "browser.actions.share",
    icon: "share",
    group: "share",
    shortcut: "s",
    when: (sel, _folder, env) => {
      if (sel.length === 0 || env.guest || !allowed(sel, "share")) return HIDDEN;
      if (sel.length > MAX_SHARE) return no(t("browser.reasons.shareTooMany", { max: MAX_SHARE }));
      return OK;
    },
    run: (sel, ctx) => ctx.ui.share(sel),
  },
  {
    id: "addToAlbum",
    labelKey: "browser.actions.addToAlbum",
    icon: "album",
    group: "share",
    shortcut: "shift+a",
    when: (sel, _folder, env) => {
      if (sel.length === 0 || env.guest) return HIDDEN;
      if (!sel.every((n) => permissionOf(n) === "owner")) return HIDDEN;
      return sel.every(isMedia) ? OK : no(t("browser.reasons.notMedia"));
    },
    run: (sel, ctx) => ctx.ui.addToAlbum(sel),
  },
  {
    id: "versions",
    labelKey: "browser.actions.versions",
    icon: "history",
    group: "info",
    shortcut: "v",
    when: (sel) => (single(sel) && sel[0].kind === "file" ? OK : HIDDEN),
    run: (sel, ctx) => ctx.ui.versions(sel[0]),
  },
  {
    id: "trash",
    labelKey: "browser.actions.trash",
    icon: "trash",
    group: "trash",
    danger: true,
    shortcut: ["delete", "mod+backspace"],
    when: (sel) => (sel.length === 0 ? HIDDEN : allowed(sel, "trash") ? OK : no(changeReason(sel))),
    run: (sel, ctx) => ctx.ui.trash(sel),
  },
];

const registry: BrowserAction[] = [...BUILT_IN];
const listeners = new Set<() => void>();
let version = 0;

/** Een actie erbij (fase 10: omzetten); geeft de functie om hem weer weg te halen. */
export function registerAction(action: BrowserAction): () => void {
  if (registry.some((a) => a.id === action.id)) throw new Error(`actie ${action.id} bestaat al`);
  registry.push(action);
  version++;
  for (const l of [...listeners]) l();
  return () => {
    const i = registry.indexOf(action);
    if (i >= 0) registry.splice(i, 1);
    version++;
    for (const l of [...listeners]) l();
  };
}

export function subscribeActions(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function actionsVersion(): number {
  return version;
}

/** Alle acties, op groep (in de volgorde van GROUPS) en daarbinnen in volgorde van registratie. */
export function browserActions(): readonly BrowserAction[] {
  return [...registry].sort((a, b) => GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group));
}

export function findAction(id: string): BrowserAction | undefined {
  return registry.find((a) => a.id === id);
}

/** De tekst van een actie voor deze selectie. */
export function actionLabel(action: BrowserAction, sel: readonly FileNode[]): string {
  return t(typeof action.labelKey === "function" ? action.labelKey(sel) : action.labelKey);
}

export function firstCombo(action: BrowserAction): Combo | undefined {
  const c = action.shortcut;
  return typeof c === "string" ? c : c?.[0];
}

/** Of een actie kan en zichtbaar is voor deze selectie (een leeg resultaat zonder selectie,
 * tenzij de actie ook zonder selectie werkt). */
export function availability(action: BrowserAction, sel: readonly FileNode[], env: ActionEnv): Availability {
  if (sel.length === 0 && !action.withoutSelection) return HIDDEN;
  return action.when(sel, env.folder, env);
}

/**
 * De menu-items voor een selectie: alleen wat zichtbaar is, met een scheidingslijn tussen de
 * groepen; wat niet kan, uitgeschakeld met de reden. run(action) doet de actie.
 */
export function actionMenuItems(
  sel: readonly FileNode[],
  env: ActionEnv,
  run: (action: BrowserAction) => void,
): MenuItem[] {
  const items: MenuItem[] = [];
  let group: ActionGroup | null = null;
  for (const action of browserActions()) {
    const a = availability(action, sel, env);
    if (!a.visible) continue;
    items.push({
      id: action.id,
      label: actionLabel(action, sel),
      icon: action.icon,
      shortcut: firstCombo(action),
      disabled: !a.enabled,
      reason: a.reason,
      danger: action.danger,
      separatorBefore: group !== null && group !== action.group,
      onSelect: () => run(action),
    });
    group = action.group;
  }
  return items;
}
