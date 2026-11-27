// Tekst bewerken in de viewer (0.8.3), zonder DOM, zodat het te testen is. TextView.tsx tekent de
// toestand en geeft de toetsen en knoppen door.
//
// - Wanneer: ingelogd (niet via een publieke link), schrijfrecht, geen eerdere versie, niet in de
//   prullenbak, en hooguit MAX_EDIT_BYTES. Wat de server weet (de deling is net ingetrokken), zegt
//   het opslaan zelf.
// - Laden: de hele inhoud (niet de eerste 512 KB zoals het lezen). Alleen UTF-8 (met of zonder
//   BOM): een andere codering zou bij opslaan veranderen, dan kan bewerken niet (met een reden).
// - Opslaan (de knop of Ctrl+S): PUT /files/{id}/content (putContent); de vorige inhoud
//   wordt een versie. Een BOM en de regeleinden (CRLF of LF) blijven zoals ze waren: de textarea
//   werkt met \n, en bij opslaan komen ze terug.
// - Typen tijdens het opslaan mag: wat erbij kwam, blijft in het veld (en is nog niet opgeslagen).
// - Wie anders opslaat: opslaan stuurt de etag mee waar de tekst op voortbouwt (If-Match); is de
//   inhoud intussen een andere, dan weigert de server (412 content_changed) en blijft de tekst in
//   het veld. Ziet de viewer een andere etag terwijl je bewerkt (seen), dan meldt hij dat
//   (changedOutside) in plaats van opnieuw te beginnen; "Toch opslaan" (overwrite) slaat op
//   zonder If-Match, "Opnieuw laden" (reload) gooit de eigen wijzigingen weg.
// - Weggaan met wijzigingen die niet zijn opgeslagen (sluiten, naar een ander bestand, terug in de
//   browser, de pagina verlaten): eerst vragen (viewer/leaveGuard.ts en beforeunload).

import { ApiError, type FileNode } from "../api";
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
  /** De inhoud op de server is niet meer die waar de tekst op voortbouwt (iemand anders sloeg
   * op, of opslaan gaf 412 content_changed) */
  changedOutside: boolean;
}

export interface EditDeps {
  /** De hele inhoud */
  load(): Promise<Uint8Array>;
  /** PUT /files/{id}/content; ifMatch: de etag waar de tekst op voortbouwt (undefined: altijd) */
  save(body: Blob, ifMatch?: string): Promise<FileNode>;
  /** Een fout als tekst (i18n errorText) */
  errorText(e: unknown): string;
}

/** PUT /files/{id}/content met If-Match (zoals api.replaceContent, die geen headers meegeeft:
 * api.ts zit in de eerste lading, en die zit op zijn budget). */
export async function putContent(
  api: { rawRequest(path: string, init?: RequestInit): Promise<Response> },
  id: string,
  body: Blob,
  ifMatch?: string,
): Promise<FileNode> {
  const res = await api.rawRequest(`/api/v1/files/${id}/content`, {
    method: "PUT",
    body,
    headers: ifMatch ? { "If-Match": ifMatch } : undefined,
  });
  return (await res.json()) as FileNode;
}

/** Bewerken van één tekstbestand: lezen → laden → bewerken ↔ opslaan → lezen. */
export class TextEditSession {
  private state: EditState = {
    phase: "reading",
    text: "",
    saved: "",
    problem: null,
    error: null,
    changedOutside: false,
  };
  private format: TextFormat = { bom: false, crlf: false };
  /** De etag van de inhoud waar de tekst op voortbouwt (bij laden, daarna die van het opslaan) */
  private base: string | undefined;
  /** De laatste etag die de viewer van het bestand kreeg */
  private latest: string | undefined;
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

  /** De etag die de viewer nu van het bestand heeft (een nieuwe lijst na node.changed). Een
   * andere dan waar de tekst op voortbouwt: changedOutside, de tekst blijft. */
  seen(etag: string | undefined): void {
    this.latest = etag;
    this.recheck();
  }

  private recheck(): void {
    const s = this.state;
    // Tijdens het opslaan kan de etag van het eigen opslaan er al zijn: pas daarna vergelijken
    if (s.phase !== "editing") return;
    const outside = this.latest !== undefined && this.base !== undefined && this.latest !== this.base;
    if (outside && !s.changedOutside) this.set({ changedOutside: true });
  }

  /** Bewerken beginnen: de hele inhoud laden. etag: van de inhoud zoals de viewer hem nu kent. */
  async start(etag?: string): Promise<boolean> {
    if (this.state.phase !== "reading") return this.state.phase === "editing";
    this.base = etag;
    this.latest = etag;
    this.set({ phase: "loading", error: null, problem: null, changedOutside: false });
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

  /** Het veld veranderde; ook tijdens het opslaan (dat slaat op wat er toen stond). */
  change(text: string): void {
    if (this.state.phase !== "editing" && this.state.phase !== "saving") return;
    this.set({ text });
  }

  /** Opslaan; geeft het bestand zoals de server het teruggaf, en de bytes die er nu staan. Zonder
   * wijzigingen: niets (null). Met If-Match op de etag waar de tekst op voortbouwt; is die er
   * niet meer (412): changedOutside, en de tekst blijft. */
  save(): Promise<{ node: FileNode; bytes: Uint8Array } | null> {
    return this.put(false);
  }

  /** "Toch opslaan": zonder If-Match, over wat er intussen op de server staat heen (dat blijft
   * als versie bewaard). */
  overwrite(): Promise<{ node: FileNode; bytes: Uint8Array } | null> {
    return this.put(true);
  }

  private async put(force: boolean): Promise<{ node: FileNode; bytes: Uint8Array } | null> {
    const s = this.state;
    if (s.phase !== "editing" || (s.text === s.saved && !force)) return null;
    const text = s.text;
    const bytes = writeText(text, this.format);
    this.set({ phase: "saving", error: null });
    try {
      const body = new Blob([bytes as Uint8Array<ArrayBuffer>], { type: "text/plain" });
      const before = this.base;
      const node = await this.deps.save(body, force ? undefined : this.base);
      this.base = node.etag;
      // Wat de viewer eerder zag, is hiermee voorbij: de oude etag (de lijst loopt nog achter), of
      // bij "Toch opslaan" ook die van de ander
      if (force || this.latest === before) this.latest = node.etag;
      // Alleen wat er werd opgeslagen; wat je intussen typte, blijft in het veld
      this.set({ phase: "editing", saved: text, changedOutside: false });
      this.recheck();
      return { node, bytes };
    } catch (e) {
      const changed = e instanceof ApiError && e.code === "content_changed";
      this.set({
        phase: "editing",
        error: this.deps.errorText(e),
        changedOutside: changed || this.state.changedOutside,
      });
      this.recheck();
      return null;
    }
  }

  /** "Opnieuw laden": de eigen wijzigingen weg, de inhoud van nu laden (etag: die van nu). */
  async reload(etag?: string): Promise<boolean> {
    if (this.state.phase !== "editing") return false;
    this.stop();
    return this.start(etag);
  }

  /** Terug naar lezen (de vraag of wijzigingen weg mogen, stelt de pagina eerst). */
  stop(): void {
    this.set({ phase: "reading", text: "", saved: "", error: null, changedOutside: false });
  }
}
