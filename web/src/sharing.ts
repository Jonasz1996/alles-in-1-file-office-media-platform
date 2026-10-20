// Delen: wie wat mag, het pad boven een gedeelde map en vervaldatums. Losse functies, zonder
// React, zodat ze te testen zijn.

import type { FileNode, NewLink, Permission, Share, ShareChange } from "./api";

/** Wat jij met een map of bestand mag. Oudere servers sturen het niet: dan is het van jou. */
export function permissionOf(node: Pick<FileNode, "permission"> | null | undefined): Permission {
  return node?.permission ?? "owner";
}

export function canWrite(permission: Permission): boolean {
  return permission === "owner" || permission === "write";
}

/** Van iemand anders (met jou gedeeld)? */
export function isShared(node: Pick<FileNode, "permission"> | null | undefined): boolean {
  return permissionOf(node) !== "owner";
}

export const PERMISSION_LABELS: Record<"read" | "write", string> = {
  read: "Kan bekijken",
  write: "Kan bewerken",
};

/** In de toolbar van een map: wat er mag in de map die open staat. */
export function folderActions(folder: FileNode | null | undefined) {
  const permission = permissionOf(folder);
  const write = folder ? canWrite(permission) : false;
  return {
    upload: write,
    mkdir: write,
    /** Je eigen hoofdmap deel je niet; een map erin wel. */
    share: Boolean(folder) && permission === "owner" && folder!.parent_id !== null,
  };
}

/**
 * Wat er mag bij een map of bestand in een lijst. shareRoot: dit is de gedeelde map (of het
 * gedeelde bestand) zelf; die hernoemt, verplaatst of verwijdert alleen de eigenaar.
 */
export function nodeActions(node: FileNode, shareRoot = false) {
  const permission = permissionOf(node);
  const owner = permission === "owner";
  const change = canWrite(permission) && (owner || !shareRoot);
  return {
    download: node.kind === "file",
    rename: change,
    trash: change,
    move: change,
    /** Kopiëren mag met lezen; het doel moet je kunnen bewerken. */
    copy: true,
    /** Tags zijn van jezelf, ook op wat met je gedeeld is. */
    tags: true,
    versions: node.kind === "file",
    /** Terugzetten of een nieuwe versie uploaden */
    versionWrite: canWrite(permission),
    versionDelete: owner,
    share: owner && node.parent_id !== null,
  };
}

export interface Crumb {
  label: string;
  href: string;
}

/**
 * Het pad boven een map. In je eigen bestanden begint het bij "Mijn bestanden"; in een
 * gedeelde map bij "Gedeeld met mij", gevolgd door het pad dat de server geeft (dat begint
 * bij de gedeelde map: wat erboven staat, zie je niet).
 */
export function crumbs(path: FileNode[]): Crumb[] {
  if (path.length === 0) return [];
  if (isShared(path[0])) {
    return [
      { label: "Gedeeld met mij", href: "/gedeeld" },
      ...path.map((p) => ({ label: p.name, href: `/map/${p.id}` })),
    ];
  }
  return path.map((p, i) =>
    i === 0 ? { label: "Mijn bestanden", href: "/" } : { label: p.name, href: `/map/${p.id}` },
  );
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "2026-10-31" (een datumveld) -> het eind van die dag in de eigen tijdzone, als ISO-tijd. */
export function endOfDay(date: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date.trim());
  if (!m) return null;
  const at = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 59, 999);
  // 2026-02-31 wordt anders stilletjes 3 maart
  if (at.getMonth() !== Number(m[2]) - 1 || at.getDate() !== Number(m[3])) return null;
  return at.toISOString();
}

/** ISO-tijd -> "2026-10-31" voor een datumveld (in de eigen tijdzone). */
export function dateInputValue(iso: string | null | undefined): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
}

/** Vandaag voor min= van een datumveld. */
export function today(now = Date.now()): string {
  return dateInputValue(new Date(now).toISOString());
}

/** "31 oktober 2026", met de tijd erbij als het niet het eind van de dag is. */
export function formatExpiry(iso: string): string {
  const at = new Date(iso);
  const date = at.toLocaleDateString("nl-NL", { day: "numeric", month: "long", year: "numeric" });
  if (at.getHours() === 23 && at.getMinutes() === 59) return date;
  return `${date} om ${at.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" })}`;
}

/** "verloopt op 31 oktober 2026", "verlopen", of null zonder vervaldatum. */
export function expiryLabel(iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  if (new Date(iso).getTime() <= now) return "verlopen";
  return `verloopt op ${formatExpiry(iso)}`;
}

/** De link om te versturen: het adres van deze app plus /s/<token>. */
export function linkHref(share: Pick<Share, "link">, origin: string): string {
  return share.link ? new URL(share.link.url, origin).toString() : "";
}

/** Eén regel over een link: "Alleen bekijken · met wachtwoord · verloopt op …". */
export function linkSummary(share: Share, now = Date.now()): string {
  const parts = [share.permission === "upload" ? "Alleen uploaden" : "Alleen bekijken"];
  if (share.link?.has_password) parts.push("met wachtwoord");
  const expiry = expiryLabel(share.expires_at, now);
  if (expiry) parts.push(expiry);
  return parts.join(" · ");
}

/** "1 lid", "3 leden" */
export function memberCount(n: number): string {
  return n === 1 ? "1 lid" : `${n} leden`;
}

/** Wat er in het formulier van een link staat (nieuw of wijzigen). */
export interface LinkForm {
  permission: "read" | "upload";
  /** Datumveld: "" = geen vervaldatum */
  date: string;
  /** "" = geen (nieuw) wachtwoord */
  password: string;
  /** Het wachtwoord weghalen (alleen bij wijzigen) */
  removePassword: boolean;
  label: string;
}

export const MIN_LINK_PASSWORD = 6;
export const MAX_LINK_PASSWORD = 200;

function checkPassword(password: string): void {
  if (password.length < MIN_LINK_PASSWORD) throw new Error(`Een wachtwoord heeft minstens ${MIN_LINK_PASSWORD} tekens`);
  if (password.length > MAX_LINK_PASSWORD) throw new Error(`Een wachtwoord heeft hooguit ${MAX_LINK_PASSWORD} tekens`);
}

/** De vervaldatum uit het formulier: null zonder datum. Gooit bij een onzin- of oude datum. */
function checkedExpiry(date: string, now: number): string | null {
  if (!date.trim()) return null;
  const iso = endOfDay(date);
  if (!iso) throw new Error("Dat is geen geldige datum");
  if (new Date(iso).getTime() <= now) throw new Error("Kies een datum vanaf vandaag");
  return iso;
}

/** De body voor een nieuwe link; gooit een Error met een Nederlandse melding als iets niet klopt. */
export function newLink(form: LinkForm, now = Date.now()): NewLink {
  const link: NewLink = { permission: form.permission };
  if (form.password) {
    checkPassword(form.password);
    link.password = form.password;
  }
  const expires = checkedExpiry(form.date, now);
  if (expires) link.expires_at = expires;
  if (form.label.trim()) link.label = form.label.trim();
  return link;
}

/** Alleen wat er veranderde. Een lege datum haalt de vervaldatum weg, removePassword het
 * wachtwoord, een leeg label het label. */
export function linkChange(share: Share, form: LinkForm, now = Date.now()): ShareChange {
  const change: ShareChange = {};
  if (form.date.trim() !== dateInputValue(share.expires_at)) change.expires_at = checkedExpiry(form.date, now);
  if (form.removePassword) change.password = "";
  else if (form.password) {
    checkPassword(form.password);
    change.password = form.password;
  }
  if (form.label.trim() !== (share.link?.label ?? "")) change.label = form.label.trim();
  return change;
}
