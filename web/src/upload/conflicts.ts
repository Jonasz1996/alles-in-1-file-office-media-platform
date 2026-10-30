// De vraag "N bestanden bestaan al": de wachtrij stelt hem (ask), de uploadlade toont de
// dialoog (ConflictDialog) en geeft het antwoord (answer). Zonder React, zodat de wachtrij hem
// kan stellen; staan er meer vragen klaar (twee batches tegelijk), dan na elkaar.

import type { ConflictChoice } from "./plan";

export interface ConflictItem {
  /** De naam van het bestand dat geüpload wordt */
  name: string;
  /** Het pad in de upload ("Vakantie/a.jpg"), voor de tekst */
  path: string;
  /** Wat er al staat: een bestand (dan kan vervangen) of een map */
  kind: string;
}

/** Per bestand de keuze (zelfde volgorde als de vraag), of null: de batch niet uploaden. */
export type ConflictAnswer = ConflictChoice[] | null;

export type AskConflicts = (items: ConflictItem[]) => Promise<ConflictAnswer>;

interface Question {
  items: ConflictItem[];
  resolve(answer: ConflictAnswer): void;
}

export class ConflictStore {
  private questions: Question[] = [];
  private readonly listeners = new Set<() => void>();

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  /** De vraag die nu getoond wordt (de oudste), of null. */
  current = (): readonly ConflictItem[] | null => this.questions[0]?.items ?? null;

  pending(): boolean {
    return this.questions.length > 0;
  }

  ask: AskConflicts = (items) => {
    if (items.length === 0) return Promise.resolve([]);
    return new Promise((resolve) => {
      this.questions.push({ items, resolve });
      this.changed();
    });
  };

  /** Het antwoord op de vraag die nu getoond wordt. */
  answer(answer: ConflictAnswer): void {
    const q = this.questions.shift();
    if (!q) return;
    this.changed();
    q.resolve(answer);
  }

  /** Bij uitloggen: alle vragen vervallen (geen upload). */
  cancelAll(): void {
    const all = this.questions;
    this.questions = [];
    if (all.length) this.changed();
    for (const q of all) q.resolve(null);
  }

  private changed(): void {
    for (const l of this.listeners) l();
  }
}

export const conflicts = new ConflictStore();
