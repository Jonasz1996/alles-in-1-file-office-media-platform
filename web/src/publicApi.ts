// Publieke links (/s/<token>), zonder in te loggen. De pagina opent de link met het token en
// krijgt een sleutel terug; die gaat mee met elk volgend verzoek, in de Authorization-header
// of als ?key= waar de browser geen header kan sturen (<a>, <img>, <video>). Het token zelf
// komt nooit in een API-adres. De sleutel staat per token in sessionStorage, zodat herladen
// niet opnieuw om het wachtwoord vraagt. Verloopt hij, dan opent de pagina de link opnieuw,
// met het wachtwoord van eerder als dat er is; anders vraagt ze het opnieuw.

import { ApiError, errorFromBody, type Preview, type PublicListing, type PublicShare, tusMetadata } from "./api";
import { type SendChunk, type TusApi, xhrSend } from "./upload";

type Fetch = typeof fetch;
export type KeyStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

interface Opened {
  key: string;
  expires_in: number;
  share: PublicShare;
}

interface Stored {
  key: string;
  /** Vanaf dan (ms) een nieuwe sleutel halen */
  renewAt: number;
  share: PublicShare;
}

const TUS = { "Tus-Resumable": "1.0.0" };
/** Zo lang (ms) voor het verlopen halen we al een nieuwe sleutel. */
const MARGIN = 60_000;
/**
 * uploadFile wil weten welk bestand het werd; via een link zie je dat niet. Dit staat dan
 * voor "alle bytes zijn binnen en de server heeft het bestand gemaakt".
 */
export const ARRIVED = "aangekomen";

/** Het token uit /s/<token>, of null. */
export function linkToken(pathname: string): string | null {
  return /^\/s\/([A-Za-z0-9_-]+)\/?$/.exec(pathname)?.[1] ?? null;
}

export function keyName(token: string): string {
  return `platform-link:${token}`;
}

function safeSession(): KeyStorage | undefined {
  try {
    return window.sessionStorage;
  } catch {
    return undefined; // opslag uit: dan vraagt herladen het wachtwoord opnieuw
  }
}

async function failure(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* geen JSON, bijv. bij HEAD */
  }
  return errorFromBody(res.status, body);
}

/** Wat de pagina moet laten zien na een fout bij het openen (of later). */
export type LinkProblem =
  | { kind: "password"; message: string | null }
  | { kind: "gone"; message: string }
  | { kind: "busy"; message: string }
  | { kind: "error"; message: string };

export function linkProblem(error: unknown): LinkProblem {
  const e = error instanceof ApiError ? error : new ApiError(0, (error as Error)?.message ?? String(error));
  if (e.code === "password_required") return { kind: "password", message: null };
  if (e.code === "wrong_password") return { kind: "password", message: e.message };
  if (e.code === "link_not_found" || e.code === "link_expired") return { kind: "gone", message: e.message };
  if (e.status === 404) return { kind: "gone", message: "Deze link bestaat niet (meer)" };
  if (e.status === 410) return { kind: "gone", message: "Deze link is verlopen" };
  if (e.status === 429) return { kind: "busy", message: e.message };
  if (e.status === 0) return { kind: "error", message: "Geen verbinding met de server. Probeer het opnieuw." };
  return { kind: "error", message: e.message };
}

/** Na deze fouten werkt de sleutel niet meer; de pagina moet iets anders laten zien. */
function locks(e: unknown): e is ApiError {
  return e instanceof ApiError && [401, 404, 410, 429].includes(e.status);
}

export interface PublicLinkOptions {
  fetch?: Fetch;
  /** null: niets bewaren */
  storage?: KeyStorage | null;
  now?: () => number;
  /** Stuurt één stuk van een upload (standaard xhrSend) */
  send?: SendChunk;
}

export class PublicLink implements TusApi {
  private key: string | null = null;
  private renewAt = 0;
  private password: string | undefined;
  private opening: Promise<PublicShare> | null = null;
  /** Upload-URL -> Upload-Length: klaar is als de server zoveel bytes heeft. */
  private readonly lengths = new Map<string, number>();
  private readonly fetchFn: Fetch;
  private readonly storage: KeyStorage | undefined;
  private readonly now: () => number;
  private readonly sendChunk: SendChunk;
  share: PublicShare | null = null;
  /** De sleutel werkt niet meer en opnieuw openen lukte niet (wachtwoord nodig, link weg). */
  onLocked: (error: ApiError) => void = () => {};

  constructor(
    readonly token: string,
    options: PublicLinkOptions = {},
  ) {
    this.fetchFn = options.fetch ?? ((...args) => fetch(...args));
    this.storage = options.storage === null ? undefined : (options.storage ?? safeSession());
    this.now = options.now ?? Date.now;
    this.sendChunk = options.send ?? xhrSend;
    this.load();
  }

  /** De sleutel, voor uploadFile (PATCH via XMLHttpRequest). */
  get accessToken(): string | null {
    return this.key;
  }

  private load(): void {
    try {
      const raw = this.storage?.getItem(keyName(this.token));
      if (!raw) return;
      const stored = JSON.parse(raw) as Stored;
      if (typeof stored.key !== "string" || !(stored.renewAt > this.now())) return;
      this.key = stored.key;
      this.renewAt = stored.renewAt;
      this.share = stored.share;
    } catch {
      /* kapot of niet beschikbaar: dan gewoon openen */
    }
  }

  private save(): void {
    if (!this.key || !this.share) return;
    const stored: Stored = { key: this.key, renewAt: this.renewAt, share: this.share };
    try {
      this.storage?.setItem(keyName(this.token), JSON.stringify(stored));
    } catch {
      /* opslag vol of uit */
    }
  }

  /** Sleutel en gegevens weg (link weg of verlopen, of een nieuw wachtwoord nodig). */
  forget(): void {
    this.key = null;
    this.share = null;
    try {
      this.storage?.removeItem(keyName(this.token));
    } catch {
      /* opslag niet beschikbaar */
    }
  }

  /** Opent de link. password: als de server erom vroeg; het blijft bewaard (alleen in het
   * geheugen) om de link later stil opnieuw te openen. */
  async open(password?: string): Promise<PublicShare> {
    const body = password === undefined ? { token: this.token } : { token: this.token, password };
    const res = await this.fetchFn("/api/v1/public/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "omit",
    });
    if (!res.ok) {
      const error = await failure(res);
      if (error.status !== 429) this.forget(); // 429: straks gewoon opnieuw, de sleutel kan nog werken
      throw error;
    }
    const opened = (await res.json()) as Opened;
    const lifetime = Math.max(0, opened.expires_in) * 1000;
    this.key = opened.key;
    // Ruim voor het verlopen een nieuwe; een sleutel die maar kort geldt (de link verloopt
    // bijna) halverwege
    this.renewAt = this.now() + Math.max(lifetime - MARGIN, lifetime / 2);
    this.share = opened.share;
    if (password !== undefined) this.password = password;
    this.save();
    return opened.share;
  }

  /** Opnieuw openen met het wachtwoord van eerder; gelijktijdige aanroepen delen één verzoek. */
  reopen(): Promise<PublicShare> {
    this.opening ??= this.open(this.password)
      .catch((e: unknown) => {
        let error = e;
        if (e instanceof ApiError && e.code === "wrong_password") {
          // Het bewaarde wachtwoord werkt niet meer (de eigenaar veranderde het): opnieuw vragen
          this.password = undefined;
          error = new ApiError(401, "Voor deze link is een wachtwoord nodig", "password_required");
        }
        if (locks(error)) this.onLocked(error);
        throw error;
      })
      .finally(() => {
        this.opening = null;
      });
    return this.opening;
  }

  /** Voor uploadFile: na een 401 tijdens het uploaden. */
  refresh = async (): Promise<boolean> => {
    try {
      await this.reopen();
      return true;
    } catch {
      return false;
    }
  };

  /** Een sleutel die nog even geldt; vlak voor het verlopen eerst een nieuwe. */
  async freshKey(): Promise<string> {
    if (!this.key || this.now() >= this.renewAt) await this.reopen();
    return this.key!;
  }

  private async raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
    const key = await this.freshKey();
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${key}`);
    const res = await this.fetchFn(path, { ...init, headers, credentials: "omit" });
    if (res.status === 401 && retry) {
      // Verlopen, of het wachtwoord is veranderd: opnieuw openen en nog één keer
      if (this.key === key) this.key = null;
      return this.raw(path, init, false);
    }
    if (!res.ok) {
      const error = await failure(res);
      if (error.code === "link_not_found" || error.code === "link_expired" || error.status === 401) {
        this.forget();
        this.onLocked(error);
      }
      throw error;
    }
    return res;
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    return (await (await this.raw(path, init)).json()) as T;
  }

  /** Een map binnen de gedeelde map; zonder folder de gedeelde map zelf. */
  children = (folder?: string) =>
    this.json<PublicListing>(`/api/v1/public/children${folder ? `?${new URLSearchParams({ folder })}` : ""}`);

  previews = (nodeIds: string[]) =>
    this.json<Record<string, Preview>>("/api/v1/public/previews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ node_ids: nodeIds }),
    });

  /** Adres om te downloaden of (inline) te tonen, met de sleutel erin. */
  async downloadUrl(nodeId: string, inline = false): Promise<string> {
    const params = new URLSearchParams({ key: await this.freshKey() });
    if (inline) params.set("inline", "true");
    return `/api/v1/public/download/${nodeId}?${params}`;
  }

  // tus via de link: het doel is altijd de gedeelde map, dus parent doet er niet toe.
  async tusCreate(_parent: string, name: string, size: number): Promise<{ url: string; nodeId: string | null }> {
    const res = await this.raw("/api/v1/public/tus", {
      method: "POST",
      headers: { ...TUS, "Upload-Length": String(size), "Upload-Metadata": tusMetadata({ filename: name }) },
    });
    const url = res.headers.get("Location");
    if (!url) throw new ApiError(res.status, "Geen Location in het antwoord");
    this.lengths.set(url, size);
    // Ook een leeg bestand: de PATCH van 0 bytes daarna bevestigt dat het er is
    return { url, nodeId: null };
  }

  async tusStatus(url: string): Promise<{ offset: number; nodeId: string | null }> {
    let res: Response;
    try {
      res = await this.raw(url, { method: "HEAD", headers: TUS });
    } catch (e) {
      // HEAD heeft geen body: 413 kan alleen het quotum van de eigenaar zijn
      if (e instanceof ApiError && e.status === 413) throw new ApiError(413, "Onvoldoende quotum");
      throw e;
    }
    const length = res.headers.get("Upload-Length");
    if (length !== null) this.lengths.set(url, Number(length));
    // Geen "klaar" op een HEAD: misschien rondt een ander verzoek het nog af (en mislukt
    // dat). Een PATCH op het eind bevestigt het.
    return { offset: Number(res.headers.get("Upload-Offset") ?? 0), nodeId: null };
  }

  async tusCancel(url: string): Promise<void> {
    await this.raw(url, { method: "DELETE", headers: TUS });
    this.lengths.delete(url);
  }

  /** Een stuk sturen. Een 204 met alle bytes binnen betekent: het bestand staat in de map. */
  send: SendChunk = async (url, offset, chunk, token, onProgress, signal) => {
    const result = await this.sendChunk(url, offset, chunk, token, onProgress, signal);
    const length = this.lengths.get(url);
    const done = result.nodeId !== null || (length !== undefined && result.offset >= length);
    return { offset: result.offset, nodeId: done ? ARRIVED : null };
  };
}
