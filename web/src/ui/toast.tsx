// Meldingen (toasts) rechtsonder, en announce() voor schermlezers (aria-live). De logica zit
// in ToastStore en LiveAnnouncer, zonder DOM, zodat ze te testen zijn.

import { type JSX, useSyncExternalStore } from "react";
import { t } from "../i18n";
import { IconButton } from "./Button";

export interface ToastAction {
  label: string;
  run(): void;
}

export interface ToastOptions {
  kind?: "info" | "ok" | "error";
  action?: ToastAction;
  /** 0 = blijft staan tot hij gesloten wordt */
  timeoutMs?: number;
  /** Een toast met hetzelfde id vervangt de vorige (bijv. voortgang) */
  id?: string;
}

export interface ToastItem {
  id: string;
  message: string;
  kind: "info" | "ok" | "error";
  action?: ToastAction;
  timeoutMs: number;
}

/** Hoe lang een toast standaard blijft: een fout of een toast met actie (Ongedaan maken) langer. */
export function defaultTimeout(o: ToastOptions): number {
  if (o.timeoutMs !== undefined) return o.timeoutMs;
  if (o.action) return 8000;
  return o.kind === "error" ? 8000 : 5000;
}

const MAX_TOASTS = 4;

export class ToastStore {
  private items: readonly ToastItem[] = [];
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly listeners = new Set<() => void>();
  private seq = 0;

  getSnapshot = (): readonly ToastItem[] => this.items;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private set(items: readonly ToastItem[]): void {
    this.items = items;
    for (const l of this.listeners) l();
  }

  /** Toont een melding; geeft de functie om hem te sluiten. */
  show(message: string, o: ToastOptions = {}): () => void {
    const id = o.id ?? `toast-${++this.seq}`;
    const item: ToastItem = { id, message, kind: o.kind ?? "info", action: o.action, timeoutMs: defaultTimeout(o) };
    const others = this.items.filter((x) => x.id !== id);
    // Te veel tegelijk: de oudste gaan weg
    const kept = others.slice(Math.max(0, others.length - (MAX_TOASTS - 1)));
    for (const gone of others) if (!kept.includes(gone)) this.clearTimer(gone.id);
    this.set([...kept, item]);
    this.startTimer(item);
    return () => this.dismiss(id);
  }

  dismiss(id: string): void {
    this.clearTimer(id);
    if (this.items.some((x) => x.id === id)) this.set(this.items.filter((x) => x.id !== id));
  }

  /** De actie uitvoeren en de toast sluiten. */
  runAction(id: string): void {
    const item = this.items.find((x) => x.id === id);
    this.dismiss(id);
    item?.action?.run();
  }

  /** Niet laten verdwijnen zolang de muis of de focus erop staat. */
  pause(id: string): void {
    this.clearTimer(id);
  }

  resume(id: string): void {
    const item = this.items.find((x) => x.id === id);
    if (item && !this.timers.has(id)) this.startTimer(item);
  }

  private startTimer(item: ToastItem): void {
    this.clearTimer(item.id);
    if (item.timeoutMs > 0) this.timers.set(item.id, setTimeout(() => this.dismiss(item.id), item.timeoutMs));
  }

  private clearTimer(id: string): void {
    const timer = this.timers.get(id);
    if (timer !== undefined) clearTimeout(timer);
    this.timers.delete(id);
  }
}

/** De tekst in de live-regio's. Dezelfde tekst twee keer na elkaar wordt anders niet opnieuw
 * voorgelezen: dan wisselt er een onzichtbaar teken achter. */
export class LiveAnnouncer {
  private state = { polite: "", assertive: "" };
  private readonly listeners = new Set<() => void>();

  getSnapshot = () => this.state;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  announce(msg: string, politeness: "polite" | "assertive" = "polite"): void {
    const prev = this.state[politeness];
    const text = prev === msg ? `${msg} ` : msg;
    this.state = { ...this.state, [politeness]: text };
    for (const l of this.listeners) l();
  }
}

export const toasts = new ToastStore();
export const announcer = new LiveAnnouncer();

/** Een melding rechtsonder; ook voorgelezen (een fout direct). Geeft de functie om hem te sluiten. */
export function toast(msg: string, o?: ToastOptions): () => void {
  return toasts.show(msg, o);
}

/** Iets voorlezen zonder het te tonen (na navigeren, na een actie). */
export function announce(msg: string, politeness: "polite" | "assertive" = "polite"): void {
  announcer.announce(msg, politeness);
}

/** De meldingen en de live-regio's; één keer in de app. */
export function ToastRegion(): JSX.Element {
  const items = useSyncExternalStore(toasts.subscribe, toasts.getSnapshot, toasts.getSnapshot);
  const live = useSyncExternalStore(announcer.subscribe, announcer.getSnapshot, announcer.getSnapshot);
  return (
    <>
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {live.polite}
      </div>
      <div className="sr-only" aria-live="assertive" aria-atomic="true">
        {live.assertive}
      </div>
      <section className="toasts" aria-label={t("common.toast.region")}>
        {items.map((item) => (
          <div
            key={item.id}
            className={`toast ${item.kind}`}
            role={item.kind === "error" ? "alert" : "status"}
            onMouseEnter={() => toasts.pause(item.id)}
            onMouseLeave={() => toasts.resume(item.id)}
            onFocus={() => toasts.pause(item.id)}
            onBlur={() => toasts.resume(item.id)}
          >
            <span className="toast-message">{item.message}</span>
            {item.action && (
              <button type="button" className="link" onClick={() => toasts.runAction(item.id)}>
                {item.action.label}
              </button>
            )}
            <IconButton
              icon="close"
              className="ghost"
              label={t("common.toast.dismiss")}
              onClick={() => toasts.dismiss(item.id)}
            />
          </div>
        ))}
      </section>
    </>
  );
}
