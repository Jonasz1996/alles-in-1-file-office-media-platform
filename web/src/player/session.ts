// De sessie van de speler (Keuze 32): play, vernieuwen, sluiten, en wat de speler doet bij elke
// fout. Pure regels plus een herstel-lus met ingespoten klok, slaap en gezondheid, zodat
// session.test.ts ze zonder netwerk en zonder echte tijd kan nalopen.
//
// - 410 media_session_gone: stil een nieuwe sessie op dezelfde plek (met replace), hooguit 3 keer
//   per minuut; daarna zou het een lus zijn, en stopt hij met de fout;
// - 403 media_access_gone: stoppen en zeggen waarom (revoked, stopped, blocked), nooit opnieuw;
// - 503 media_busy, media_timeout en 409 media_probe_pending: na Retry-After opnieuw, met een
//   melding, begrensd;
// - een netwerkfout, of 502/503/504 zonder code (de app herstart of wordt bijgewerkt): opnieuw na
//   1, 2, 4 en 8 s en dan elke 10 s, zolang er buffer is plus 120 s. Ondertussen kijkt hij naar
//   /api/v1/health: was die weg en is hij er weer, dan meteen opnieuw. Komt er dan 410, dan een
//   nieuwe sessie op de positie van nu. Pas daarna "De server is niet bereikbaar".
//
// De sid staat nooit in localStorage, een log of een melding (Keuze 35).

import { ApiError, errorFromBody } from "../api";
import { isNetworkError } from "../i18n";
import { closeSession } from "../media/api";
import type { ClientProfile, MediaErrorParams, PlayIn, Quality } from "../media/types";

/** Wat de kijker koos; elke wissel is een nieuwe sessie met replace (Keuze 32). */
export interface Choice {
  /** Stream.index van het audiospoor */
  audio?: number | null;
  /** Alleen een spoor dat ingebrand wordt (state burn, of forced_available); tekst gaat als <track> */
  burn?: string | null;
  quality?: Quality;
  hdr?: "raw";
}

/** De body van play: de keuze, de start in ms en (bij een wissel of na 410) de oude sid. */
export function playBody(profile: ClientProfile, choice: Choice, startS: number, replace?: string | null): PlayIn {
  const body: PlayIn = { profile };
  if (choice.audio != null) body.audio = choice.audio;
  if (choice.burn) body.subtitle = choice.burn;
  if (choice.quality && choice.quality !== "auto") body.quality = choice.quality;
  if (choice.hdr) body.hdr = choice.hdr;
  const start = Math.max(0, Math.round((Number.isFinite(startS) ? startS : 0) * 1000));
  if (start > 0) body.start_ms = start;
  if (replace) body.replace = replace;
  return body;
}

/** Een fout van een adres onder /api/v1/stream/ (een segment, de init, het bestand). Net als
 * ApiError, plus Retry-After uit de kop. */
export class StreamError extends ApiError {
  constructor(
    status: number,
    message: string,
    code?: string,
    params?: Record<string, unknown>,
    public retryAfter: number | null = null,
  ) {
    super(status, message, code, params);
  }
}

/** De fout uit een antwoord dat niet ok is (de body als JSON als die er is). */
export async function responseError(res: Response): Promise<StreamError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // geen JSON (een proxy, of een lege body)
  }
  const e = errorFromBody(res.status, body);
  return new StreamError(res.status, e.message, e.code, e.params, retryAfter(res.headers.get("Retry-After")));
}

/** Retry-After in seconden (alleen de vorm met een getal), of null. */
export function retryAfter(value: string | null): number | null {
  if (value === null || !/^\s*\d+(\.\d+)?\s*$/.test(value)) return null;
  return Number(value);
}

/** Afgebroken (zoeken, sluiten): geen fout. */
export function isAbort(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as { name?: unknown }).name === "AbortError";
}

export type StopReason = MediaErrorParams["media_access_gone"]["reason"];

export type Failure =
  | { kind: "renew" }
  | { kind: "stop"; reason: StopReason }
  | { kind: "wait"; seconds: number }
  | { kind: "retry" }
  | { kind: "fatal" };

const STOP_REASONS: readonly StopReason[] = ["revoked", "stopped", "blocked"];
/** Codes waarbij wachten helpt (Retry-After). */
const WAIT_CODES = new Set(["media_busy", "media_timeout", "media_probe_pending"]);
/** Zonder code (of de nood-code van api.ts): de app zelf is even weg. */
const NO_CODE = new Set([undefined, "", "http_error"]);

/** Wat de speler met een fout doet (Keuze 32). */
export function classify(e: unknown): Failure {
  if (isNetworkError(e)) return { kind: "retry" };
  const err = (typeof e === "object" && e !== null ? e : {}) as {
    status?: unknown;
    code?: unknown;
    params?: Record<string, unknown>;
    retryAfter?: unknown;
  };
  if (typeof err.status !== "number") return { kind: "fatal" };
  const status = err.status;
  const code = typeof err.code === "string" ? err.code : undefined;
  if (status === 0) return { kind: "retry" };
  if (status === 410) return { kind: "renew" };
  if (status === 403 && code === "media_access_gone") {
    const reason = err.params?.reason;
    return { kind: "stop", reason: STOP_REASONS.includes(reason as StopReason) ? (reason as StopReason) : "revoked" };
  }
  if (code && WAIT_CODES.has(code)) {
    const fromHeader = typeof err.retryAfter === "number" ? err.retryAfter : null;
    const fromParams = Number(err.params?.retry_after);
    const seconds = fromHeader ?? (Number.isFinite(fromParams) && fromParams > 0 ? fromParams : 2);
    return { kind: "wait", seconds: Math.max(1, Math.min(60, seconds)) };
  }
  if (code === "media_worker_down") return { kind: "retry" };
  if ((status === 502 || status === 503 || status === 504) && NO_CODE.has(code)) return { kind: "retry" };
  return { kind: "fatal" };
}

// --- de regels ---

/** Opnieuw na 1, 2, 4 en 8 s, en dan elke 10 s. */
export const RETRY_DELAYS_S: readonly number[] = [1, 2, 4, 8];
export const RETRY_EVERY_S = 10;
/** Zo lang na de buffer blijft hij het proberen. */
export const GRACE_S = 120;
/** Hooguit zoveel nieuwe sessies na 410 in RENEW_WINDOW_MS. */
export const RENEW_MAX = 3;
export const RENEW_WINDOW_MS = 60_000;
/** Hooguit zoveel keer wachten op Retry-After na elkaar (bezet, de probe loopt nog). */
export const WAIT_MAX = 30;
/** Zo vaak kijkt hij naar /api/v1/health tijdens het wachten. */
export const HEALTH_EVERY_MS = 1000;

export function retryDelay(attempt: number): number {
  return (RETRY_DELAYS_S[attempt] ?? RETRY_EVERY_S) * 1000;
}

/** Hooguit RENEW_MAX vernieuwingen per minuut: anders een lus. */
export class RenewLimiter {
  private times: number[] = [];

  allow(now: number): boolean {
    this.times = this.times.filter((t) => now - t < RENEW_WINDOW_MS);
    if (this.times.length >= RENEW_MAX) return false;
    this.times.push(now);
    return true;
  }
}

export type Notice = { kind: "reconnecting" } | { kind: "busy"; error: unknown } | null;

export interface RecoverDeps {
  /** Wat mislukte nog eens (het segment, het bestand) */
  attempt(): Promise<void>;
  /** Een nieuwe sessie op de positie van nu (play met replace) */
  renew(): Promise<void>;
  /** Seconden buffer voor de positie */
  ahead(): number;
  /** Is /api/v1/health goed? */
  health(): Promise<boolean>;
  sleep(ms: number): Promise<void>;
  now(): number;
  notice(n: Notice): void;
  /** Gestopt (een andere keuze, de pagina weg) */
  cancelled?(): boolean;
}

export type Outcome =
  | { ok: true; renewed: boolean }
  | { ok: false; stop: StopReason }
  | { ok: false; unreachable: true; error: unknown }
  | { ok: false; error: unknown; loop?: true }
  | { ok: false; cancelled: true };

/**
 * De herstel-lus van Keuze 32. Eén Recovery per speler: de grens op vernieuwen telt over alle
 * fouten heen.
 */
export class Recovery {
  constructor(private readonly limiter = new RenewLimiter()) {}

  async run(first: unknown, d: RecoverDeps): Promise<Outcome> {
    let err = first;
    let tries = 0;
    let waits = 0;
    let deadline: number | null = null;
    let renewing = false;
    for (;;) {
      if (d.cancelled?.()) return { ok: false, cancelled: true };
      const f = classify(err);
      if (f.kind === "stop") {
        d.notice(null);
        return { ok: false, stop: f.reason };
      }
      if (f.kind === "fatal") {
        d.notice(null);
        return { ok: false, error: err };
      }
      if (f.kind === "renew") {
        // Elke 410 telt, ook een tweede in dezelfde ronde (de nieuwe sessie was meteen weer weg):
        // anders vernieuwt hij in een strakke lus
        if (!this.limiter.allow(d.now())) {
          d.notice(null);
          return { ok: false, error: err, loop: true };
        }
        renewing = true;
      } else if (f.kind === "wait") {
        if (++waits > WAIT_MAX) {
          d.notice(null);
          return { ok: false, error: err };
        }
        d.notice({ kind: "busy", error: err });
        await d.sleep(f.seconds * 1000);
      } else {
        deadline ??= d.now() + (Math.max(0, d.ahead()) + GRACE_S) * 1000;
        if (d.now() >= deadline) {
          d.notice(null);
          return { ok: false, unreachable: true, error: err };
        }
        d.notice({ kind: "reconnecting" });
        await this.pause(retryDelay(tries++), deadline, d);
      }
      if (d.cancelled?.()) return { ok: false, cancelled: true };
      try {
        await (renewing ? d.renew() : d.attempt());
        d.notice(null);
        return { ok: true, renewed: renewing };
      } catch (e) {
        if (isAbort(e)) return { ok: false, cancelled: true };
        err = e;
      }
    }
  }

  /** ms wachten, of korter: tot de deadline, of tot de gezondheid terug is nadat hij weg was. */
  private async pause(ms: number, deadline: number, d: RecoverDeps): Promise<void> {
    const until = Math.min(d.now() + ms, deadline);
    let down = false;
    while (d.now() < until) {
      await d.sleep(Math.min(HEALTH_EVERY_MS, until - d.now()));
      if (d.cancelled?.() || d.now() >= until) return;
      let ok = false;
      try {
        ok = await d.health();
      } catch {
        ok = false;
      }
      if (!ok) down = true;
      else if (down) return;
    }
  }
}

/** GET /api/v1/health, hooguit 3 s. Zonder login: de gezondheid van de app zelf. */
export async function health(fetchFn: typeof fetch = (...a) => fetch(...a)): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetchFn("/api/v1/health", { cache: "no-store", credentials: "omit", signal: ctrl.signal });
    void res.body?.cancel();
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export const sleep = (ms: number): Promise<void> => new Promise((ok) => setTimeout(ok, ms));

/** Een sessie sluiten zonder te wachten (een wissel, de pagina weg). Fouten maken niet uit: de
 * server ruimt een sessie zonder verzoeken toch op. */
export function closeQuietly(sid: string | null | undefined): void {
  if (!sid) return;
  void closeSession(sid).catch(() => {});
}
