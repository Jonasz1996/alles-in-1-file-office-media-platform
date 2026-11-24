// Tekst bewerken in de viewer (0.8.3), zonder DOM, zodat het te testen is. TextView.tsx tekent de
// toestand en geeft de toetsen en knoppen door.
//
// - Wanneer: ingelogd (niet via een publieke link), schrijfrecht, geen eerdere versie, niet in de
//   prullenbak, en hooguit MAX_EDIT_BYTES. Wat de server weet (de deling is net ingetrokken), zegt
//   het opslaan zelf.
// - Laden: de hele inhoud (niet de eerste 512 KB zoals het lezen). Alleen UTF-8 (met of zonder
//   BOM): een andere codering zou bij opslaan veranderen, dan kan bewerken niet (met een reden).
// - Opslaan (de knop of Ctrl+S): PUT /files/{id}/content (api.replaceContent); de vorige inhoud
//   wordt een versie. Een BOM en de regeleinden (CRLF of LF) blijven zoals ze waren: de textarea
//   werkt met \n, en bij opslaan komen ze terug.
// - Weggaan met wijzigingen die niet zijn opgeslagen (sluiten, naar een ander bestand, de pagina
//   verlaten): eerst vragen (viewer/leaveGuard.ts en beforeunload).

import type { FileNode } from "../api";
import { canWrite, permissionOf } from "../sharing";
import { matchKey } from "../ui/keys";

/** Groter bewerkt de viewer niet (een textarea met megabytes tekst is traag). */
export const MAX_EDIT_BYTES = 5 * 1024 * 1024;

const BOM = [0xef, 0xbb, 0xbf];

/** Wat de viewer van een bestand weet (een FileNode uit Bestanden heeft ook permission). */
export interface TextTarget {
  size: number;
  version?: { id: string } | null;
  permission?: FileNode["permission"];
  deleted_at?: string | null;
}

/** Kan dit bestand hier bewerkt worden? */
export function canEditText(target: TextTarget, sourceKind: string): boolean {
  if (sourceKind !== "user") return false;
  if (target.version) return false;
  if (target.deleted_at) return false;
  if (!canWrite(permissionOf(target))) return false;
  return target.size <= MAX_EDIT_BYTES;
}

/** Hoe het bestand was: met BOM, met CRLF. */
export interface TextFormat {
  bom: boolean;
  crlf: boolean;
}

/** De tekst om te bewerken (met \n), en het formaat; null als het geen UTF-8 is. */
export function readText(bytes: Uint8Array): { text: string; format: TextFormat } | null {
  const bom = bytes.length >= 3 && BOM.every((b, i) => bytes[i] === b);
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bom ? bytes.subarray(3) : bytes);
  } catch {
    return null;
  }
  const crlf = (text.match(/\r\n/g) ?? []).length;
  const lf = (text.match(/\n/g) ?? []).length - crlf;
  return { text: text.replace(/\r\n/g, "\n"), format: { bom, crlf: crlf > 0 && crlf >= lf } };
}

/** De bytes om op te slaan: UTF-8, met de BOM en de regeleinden van het formaat. */
export function writeText(text: string, format: TextFormat): Uint8Array {
  let body = text.replace(/\r\n?/g, "\n");
  if (format.crlf) body = body.replace(/\n/g, "\r\n");
  const encoded = new TextEncoder().encode(body);
  if (!format.bom) return encoded;
  const out = new Uint8Array(encoded.length + 3);
  out.set(BOM, 0);
  out.set(encoded, 3);
  return out;
}

/** Ctrl+S (Cmd+S op een Mac): opslaan. */
export function isSaveKey(
  e: { key: string; code?: string; altKey: boolean; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean },
  isMac?: boolean,
): boolean {
  return matchKey(e, "mod+s", isMac);
}

export type EditPhase = "reading" | "loading" | "editing" | "saving";

/** Waarom bewerken niet lukte (de tekst van de knop of melding komt uit i18n). */
export type EditProblem = "encoding" | "too_large" | null;

export interface EditState {
  phase: EditPhase;
  /** De tekst in het veld */
  text: string;
  /** Zoals hij het laatst is opgeslagen (of geladen) */
  saved: string;
  problem: EditProblem;
  /** Een fout van laden of opslaan (al vertaald) */
  error: string | null;
}

export interface EditDeps {
  /** De hele inhoud */
  load(): Promise<Uint8Array>;
  /** PUT /files/{id}/content */
  save(body: Blob): Promise<FileNode>;
  /** Een fout als tekst (i18n errorText) */
  errorText(e: unknown): string;
}

/** Bewerken van één tekstbestand: lezen → laden → bewerken ↔ opslaan → lezen. */
export class TextEditSession {
  private state: EditState = { phase: "reading", text: "", saved: "", problem: null, error: null };
  private format: TextFormat = { bom: false, crlf: false };
  private readonly listeners = new Set<() => void>();

  constructor(private readonly deps: EditDeps) {}

  getState = (): EditState => this.state;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private set(change: Partial<EditState>): void {
    this.state = { ...this.state, ...change };
    for (const l of [...this.listeners]) l();
  }

  /** Zijn er wijzigingen die nog niet zijn opgeslagen? */
  dirty(): boolean {
    const s = this.state;
    return (s.phase === "editing" || s.phase === "saving") && s.text !== s.saved;
  }

  /** Bewerken beginnen: de hele inhoud laden. */
  async start(): Promise<boolean> {
    if (this.state.phase !== "reading") return this.state.phase === "editing";
    this.set({ phase: "loading", error: null, problem: null });
    let bytes: Uint8Array;
    try {
      bytes = await this.deps.load();
    } catch (e) {
      this.set({ phase: "reading", error: this.deps.errorText(e) });
      return false;
    }
    if (bytes.length > MAX_EDIT_BYTES) {
      this.set({ phase: "reading", problem: "too_large" });
      return false;
    }
    const read = readText(bytes);
    if (!read) {
      this.set({ phase: "reading", problem: "encoding" });
      return false;
    }
    this.format = read.format;
    this.set({ phase: "editing", text: read.text, saved: read.text });
    return true;
  }

  change(text: string): void {
    if (this.state.phase !== "editing") return;
    this.set({ text });
  }

  /** Opslaan; geeft het bestand zoals de server het teruggaf, en de bytes die er nu staan. Zonder
   * wijzigingen: niets (null). */
  async save(): Promise<{ node: FileNode; bytes: Uint8Array } | null> {
    const s = this.state;
    if (s.phase !== "editing" || s.text === s.saved) return null;
    const text = s.text;
    const bytes = writeText(text, this.format);
    this.set({ phase: "saving", error: null });
    try {
      const node = await this.deps.save(new Blob([bytes as Uint8Array<ArrayBuffer>], { type: "text/plain" }));
      this.set({ phase: "editing", saved: text });
      return { node, bytes };
    } catch (e) {
      this.set({ phase: "editing", error: this.deps.errorText(e) });
      return null;
    }
  }

  /** Terug naar lezen (de vraag of wijzigingen weg mogen, stelt de pagina eerst). */
  stop(): void {
    this.set({ phase: "reading", text: "", saved: "", error: null });
  }
}
