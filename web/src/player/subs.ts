// Een ondertitel die nog gemaakt wordt (M5): subs/{id}.vtt geeft dan 202 met Retry-After: 5. De
// speler vraagt hem opnieuw na Retry-After (met de tekst "Ondertitel wordt voorbereid") tot hij
// er is; dan zet hij de <track> neer, die hem uit de cache van de browser leest (de VTT heeft
// Cache-Control: private, max-age=3600). Geen verbinding: na 2 s opnieuw. Begrensd.

import { retryAfter } from "./session";

export const VTT_TRIES = 60;
const NET_WAIT_S = 2;
const DEFAULT_WAIT_S = 5;

export type VttResult = "ready" | "failed" | "cancelled";

export interface VttDeps {
  fetchFn: typeof fetch;
  sleep(ms: number): Promise<void>;
  cancelled(): boolean;
  /** Bij elke 202 (de tekst "wordt voorbereid") */
  pending?(): void;
}

/** Wachten tot subs/{id}.vtt er is: ready (200), failed (een andere status, of te vaak) of cancelled. */
export async function waitVtt(url: string, d: VttDeps, tries = VTT_TRIES): Promise<VttResult> {
  for (let i = 0; i < tries; i++) {
    if (d.cancelled()) return "cancelled";
    let wait: number;
    try {
      const res = await d.fetchFn(url);
      if (res.status === 202) {
        void res.body?.cancel();
        d.pending?.();
        wait = Math.max(1, Math.min(60, retryAfter(res.headers.get("Retry-After")) ?? DEFAULT_WAIT_S));
      } else {
        // De body leest de <track> straks uit de cache
        void res.body?.cancel();
        return res.ok ? "ready" : "failed";
      }
    } catch {
      wait = NET_WAIT_S;
    }
    if (d.cancelled()) return "cancelled";
    await d.sleep(wait * 1000);
  }
  return "failed";
}
