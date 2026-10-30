// confirm() en prompt() als promise, in plaats van de dialogen van de browser: in de stijl van de
// app, vertaald en met het toetsenbord. DialogHost (één keer in de app) toont ze, één tegelijk.

import { type FormEvent, type JSX, type ReactNode, useState, useSyncExternalStore } from "react";
import { t } from "../i18n";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Field } from "./Field";

export interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  /** De tekst op de bevestigknop ("Verwijderen"), nooit alleen "OK" */
  confirm: string;
  cancel?: string;
  danger?: boolean;
  /** Pas bevestigen na het typen van deze tekst (bijv. een naam bij iets onherroepelijks) */
  typeToConfirm?: string;
}

export interface PromptOptions {
  title: string;
  label: string;
  initial?: string;
  confirm: string;
  type?: "text" | "password" | "number";
  /** Een foutmelding, of null als het goed is */
  validate?: (v: string) => string | null;
  /** Bij een bestandsnaam alleen de naam selecteren, niet de extensie */
  selectBaseName?: boolean;
}

type Request =
  | { id: number; kind: "confirm"; o: ConfirmOptions; resolve(v: boolean): void }
  | { id: number; kind: "prompt"; o: PromptOptions; resolve(v: string | null): void };

class DialogQueue {
  private queue: readonly Request[] = [];
  private readonly listeners = new Set<() => void>();
  private seq = 0;

  getSnapshot = (): readonly Request[] => this.queue;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  add(r: Omit<Request, "id">): void {
    this.queue = [...this.queue, { ...r, id: ++this.seq } as Request];
    this.changed();
  }

  done(id: number): void {
    this.queue = this.queue.filter((r) => r.id !== id);
    this.changed();
  }

  private changed(): void {
    for (const l of this.listeners) l();
  }
}

const dialogs = new DialogQueue();

/** Vraagt om bevestiging; true = bevestigd. De focus staat eerst op de veilige knop (Annuleren). */
export function confirm(o: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => dialogs.add({ kind: "confirm", o, resolve }));
}

/** Vraagt om een tekst; null = geannuleerd. */
export function prompt(o: PromptOptions): Promise<string | null> {
  return new Promise((resolve) => dialogs.add({ kind: "prompt", o, resolve }));
}

/** Het einde van de naam zonder de extensie: "foto.jpg" → 4, ".bashrc" → 7 (geen extensie). */
export function baseNameEnd(name: string): number {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? dot : name.length;
}

function ConfirmDialog({ o, onDone }: { o: ConfirmOptions; onDone(v: boolean): void }) {
  const [typed, setTyped] = useState("");
  const blocked = o.typeToConfirm !== undefined && typed !== o.typeToConfirm;
  return (
    <Dialog
      title={o.title}
      size="s"
      onClose={() => onDone(false)}
      actions={
        <>
          <Button data-autofocus="" onClick={() => onDone(false)}>
            {o.cancel ?? t("common.cancel")}
          </Button>
          <Button variant={o.danger ? "danger" : "primary"} disabled={blocked} onClick={() => onDone(true)}>
            {o.confirm}
          </Button>
        </>
      }
    >
      {o.message !== undefined && <div className="dialog-message">{o.message}</div>}
      {o.typeToConfirm !== undefined && (
        <Field label={t("common.dialog.typeToConfirm", { text: o.typeToConfirm })}>
          {(c) => (
            <input
              {...c}
              autoComplete="off"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !blocked) onDone(true);
              }}
            />
          )}
        </Field>
      )}
    </Dialog>
  );
}

function PromptDialog({ o, onDone }: { o: PromptOptions; onDone(v: string | null): void }) {
  const [value, setValue] = useState(o.initial ?? "");
  const [error, setError] = useState<string | null>(null);

  function submit(e: FormEvent) {
    e.preventDefault();
    const problem = o.validate ? o.validate(value) : value.trim() === "" ? t("common.dialog.required") : null;
    if (problem) {
      setError(problem);
      return;
    }
    onDone(value);
  }

  return (
    <Dialog title={o.title} size="s" onClose={() => onDone(null)}>
      <form onSubmit={submit}>
        <Field label={o.label} error={error}>
          {(c) => (
            <input
              {...c}
              data-autofocus=""
              type={o.type ?? "text"}
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                setError(null);
              }}
              onFocus={(e) => {
                const el = e.currentTarget;
                if (o.selectBaseName && el.type === "text") el.setSelectionRange(0, baseNameEnd(el.value));
                else el.select();
              }}
            />
          )}
        </Field>
        <div className="dialog-actions">
          <Button onClick={() => onDone(null)}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary">
            {o.confirm}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Toont de vragen van confirm() en prompt(); één keer in de app. */
export function DialogHost(): JSX.Element | null {
  const queue = useSyncExternalStore(dialogs.subscribe, dialogs.getSnapshot, dialogs.getSnapshot);
  const current = queue[0];
  if (!current) return null;
  if (current.kind === "confirm") {
    return (
      <ConfirmDialog
        key={current.id}
        o={current.o}
        onDone={(v) => {
          dialogs.done(current.id);
          current.resolve(v);
        }}
      />
    );
  }
  return (
    <PromptDialog
      key={current.id}
      o={current.o}
      onDone={(v) => {
        dialogs.done(current.id);
        current.resolve(v);
      }}
    />
  );
}
