// De stand van zoeken op beeld voor wie ingelogd is (fase 6, X3; Keuze 78 en 81): GET /photos/beeld,
// gedeeld door het raster, de schakelaar in Account → Foto's en "Meer zoals deze" in de viewer.
//
// Met de analyse uit kent de server zoeken op beeld niet (501 not_built, zoals in 0.8.0): dan is
// de stand "off" en tonen de drie plekken precies wat 0.8.0 toonde. Zo blijft golf 6 onzichtbaar
// tot de beheerder de container analyse aanzet.
//
// Eén vraag per halve minuut (de server warmt bij elke vraag hooguit eens per minuut je bestand op
// in de container); "off" blijft tien minuten (de analyse gaat pas aan na een herstart van de
// app). Na de schakelaar vergeet forgetBeeld() de stand, zodat elke plek hem opnieuw vraagt. Een
// fout (geen verbinding) bewaart niets: de volgende plek vraagt opnieuw.
//
// Dit bestand laadt ook met "Meer zoals deze" (≤ 3 kB met het gebied beeld, budget.mjs): daarom
// vraagt het GET /photos/beeld zelf met api.request (in de eerste lading) in plaats van via
// beeldStatus() in search/api.ts, en bouwt likeHref de URL zelf (beeld.test.ts toetst dat hij
// gelijk is aan queryHref).

import { useEffect, useState } from "react";
import { ApiError, api } from "../../api";
import type { BeeldState, BeeldStatus } from "../types";

/** De stand: die van de server, off als de server zoeken op beeld niet kent (501), of fout als
 * de vraag mislukte (geen verbinding): dan beslist de server bij het zoeken zelf. */
export type Stand = BeeldState | "off" | "fout";

export interface Known {
  state: Stand;
  /** 0–1 zolang je foto's bekeken worden, anders null */
  done: number | null;
}

export const OFF: Known = { state: "off", done: null };
const FOUT: Known = { state: "fout", done: null };

/** Zo lang blijft een stand bewaard; off langer. */
export const TTL_MS = 30_000;
export const OFF_TTL_MS = 600_000;

let cached: { who: string; at: number; ttl: number; value: Promise<Known> } | null = null;
const listeners = new Set<() => void>();

/** De stand van `who` (het id van wie ingelogd is): bewaard of vers gevraagd. */
export function loadBeeld(who: string, now: number = Date.now()): Promise<Known> {
  if (cached && cached.who === who && now - cached.at < cached.ttl) return cached.value;
  const entry = {
    who,
    at: now,
    ttl: TTL_MS,
    value: api.request<BeeldStatus>("GET", "/api/v1/photos/beeld").then(
      (s): Known => ({ state: s.state, done: s.done }),
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 501) {
          entry.ttl = OFF_TTL_MS;
          return OFF;
        }
        if (cached === entry) cached = null;
        throw e;
      },
    ),
  };
  cached = entry;
  return entry.value;
}

/** Vergeet de stand (na de schakelaar): elke plek die hem toont, vraagt hem opnieuw. */
export function forgetBeeld(): void {
  cached = null;
  for (const l of [...listeners]) l();
}

/** De stand in een component: null zolang hij er niet is. */
export function useBeeld(who: string | null): Known | null {
  const [known, setKnown] = useState<Known | null>(null);
  const [round, setRound] = useState(0);
  useEffect(() => {
    const again = () => setRound((r) => r + 1);
    listeners.add(again);
    return () => {
      listeners.delete(again);
    };
  }, []);
  useEffect(() => {
    if (who === null) return;
    let live = true;
    loadBeeld(who).then(
      (k) => live && setKnown(k),
      () => live && setKnown(FOUT),
    );
    return () => {
      live = false;
    };
  }, [who, round]);
  return known;
}

/** Zoeken op beeld kan voor jou: aan, ook als je foto's nog bekeken worden. */
export function canLike(k: Known | null): boolean {
  return k !== null && (k.state === "ok" || k.state === "bezig");
}

/** /zoeken?mode=image&like=<id>: "Meer zoals deze" (Keuze 81); gelijk aan
 * queryHref({ mode: "image", like: id }) in search/query.ts. */
export function likeHref(id: string): string {
  return `/zoeken?mode=image&like=${encodeURIComponent(id)}`;
}
