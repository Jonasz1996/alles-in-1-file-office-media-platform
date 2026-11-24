// Hernoemen naar een andere extensie (0.8.3). PATCH /files/{id} verandert alleen de naam: een
// .docx die .txt gaat heten, blijft een zip, en toont dan rare tekens. Daarom, vóór het hernoemen:
// - dezelfde extensie, geen extensie (ervoor of erna), of tekst naar tekst (txt, md, csv, log,
//   json, …): gewoon hernoemen, de inhoud past nog;
// - kan de editor het omzetten (office bewerkt nu, en beide in dezelfde groep: tekst, werkblad of
//   dia, zie api/app/routers/office_saveas.py): de vraag "Omzetten?". Ja maakt een omgezette kopie
//   met de nieuwe naam (POST …/convert-to); het origineel blijft. Nee: niets;
// - anders een waarschuwing dat de inhoud niet wordt omgezet, met "Toch hernoemen".
// Mappen hernoemen zonder vragen.

import type { FileNode } from "../api";

/** De extensie in kleine letters ("" zonder punt, of met alleen een punt vooraan). */
export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toLowerCase() : "";
}

/** Gewone tekst: de inhoud blijft goed bij een andere van deze extensies. */
export const PLAIN_TEXT: ReadonlySet<string> = new Set([
  "txt", "text", "md", "markdown", "csv", "tsv", "log", "json", "xml", "yaml", "yml", "ini", "conf",
  "cfg", "env", "toml", "srt", "vtt", "html", "htm", "css", "js", "mjs", "ts", "tsx", "jsx", "py",
  "sh", "sql", "c", "h", "cpp", "java", "go", "rs", "rb", "php",
]);

/** Wat de editor kan omzetten, per groep: van (bronnen) naar (doelen). Gelijk aan GROUPS in
 * office_saveas.py. */
export const CONVERT_GROUPS: readonly { from: ReadonlySet<string>; to: ReadonlySet<string> }[] = [
  { from: new Set(["docx", "doc", "odt", "rtf", "docm", "txt"]), to: new Set(["docx", "odt", "rtf", "txt", "pdf"]) },
  { from: new Set(["xlsx", "xls", "ods", "xlsm", "csv"]), to: new Set(["xlsx", "ods", "csv", "pdf"]) },
  { from: new Set(["pptx", "ppt", "odp", "pptm"]), to: new Set(["pptx", "odp", "pdf"]) },
];

/** Kan de editor van from naar to omzetten? */
export function canConvert(from: string, to: string): boolean {
  return from !== to && CONVERT_GROUPS.some((g) => g.from.has(from) && g.to.has(to));
}

export type RenamePlan =
  | { kind: "plain" }
  | { kind: "convert"; from: string; to: string }
  | { kind: "warn"; from: string; to: string };

/** Wat er moet gebeuren bij hernoemen van oldName naar newName; convert: kan de editor nu
 * omzetten (office bewerkt). */
export function renamePlan(oldName: string, newName: string, convert: boolean): RenamePlan {
  const from = extensionOf(oldName);
  const to = extensionOf(newName);
  if (from === to || from === "" || to === "") return { kind: "plain" };
  if (PLAIN_TEXT.has(from) && PLAIN_TEXT.has(to)) return { kind: "plain" };
  if (convert && canConvert(from, to)) return { kind: "convert", from, to };
  return { kind: "warn", from, to };
}

/** Een vraag met twee knoppen (ui/dialogs confirm); true = de bevestigknop. */
export interface Ask {
  title: string;
  message: string;
  confirm: string;
  danger?: boolean;
}

export interface RenameDeps {
  /** Kan de editor nu omzetten? (office status.editing) */
  canConvert(): Promise<boolean>;
  ask(a: Ask): Promise<boolean>;
  /** PATCH /files/{id} {name} */
  rename(id: string, name: string): Promise<FileNode>;
  /** POST /office/files/{id}/convert-to {format, name} */
  convertTo(id: string, format: string, name: string): Promise<FileNode>;
  /** Een tekst uit i18n */
  t(key: string, vars?: Record<string, string>): string;
}

/** Het resultaat: het hernoemde bestand, een nieuwe kopie (omgezet), of niets (geannuleerd). */
export type RenameResult = { kind: "renamed"; node: FileNode } | { kind: "converted"; node: FileNode } | { kind: "none" };

/** Hernoemt node naar name, met de vraag of waarschuwing hierboven. Fouten gaan naar de aanroeper. */
export async function renameWithCheck(node: FileNode, name: string, deps: RenameDeps): Promise<RenameResult> {
  if (node.kind !== "file") return { kind: "renamed", node: await deps.rename(node.id, name) };
  let plan = renamePlan(node.name, name, false);
  if (plan.kind === "warn" && canConvert(plan.from, plan.to) && (await deps.canConvert())) {
    plan = { kind: "convert", from: plan.from, to: plan.to };
  }
  if (plan.kind === "plain") return { kind: "renamed", node: await deps.rename(node.id, name) };
  const vars = { from: plan.from, to: plan.to, name };
  if (plan.kind === "convert") {
    const yes = await deps.ask({
      title: deps.t("browser.rename.convert.title", vars),
      message: deps.t("browser.rename.convert.message", vars),
      confirm: deps.t("browser.rename.convert.confirm"),
    });
    if (!yes) return { kind: "none" };
    return { kind: "converted", node: await deps.convertTo(node.id, plan.to, name) };
  }
  const anyway = await deps.ask({
    title: deps.t("browser.rename.warn.title", vars),
    message: deps.t("browser.rename.warn.message", vars),
    confirm: deps.t("browser.rename.warn.confirm"),
    danger: true,
  });
  if (!anyway) return { kind: "none" };
  return { kind: "renamed", node: await deps.rename(node.id, name) };
}
