// De open modale dialogen (in de top layer), van onder naar boven. Wat boven alles moet blijven
// (meldingen, live-regio's, menu's) komt in de bovenste: buiten een modale dialoog is alles inert
// en voor schermlezers weg. Zonder DOM-afhankelijkheid, zodat het te testen is.

export class LayerStack<T> {
  private stack: T[] = [];
  private readonly listeners = new Set<() => void>();

  /** Een dialoog is open; geeft de functie voor als hij sluit. */
  open(el: T): () => void {
    this.stack = [...this.stack, el];
    this.changed();
    let closed = false;
    return () => {
      if (closed) return;
      closed = true;
      const i = this.stack.lastIndexOf(el);
      if (i >= 0) this.stack = [...this.stack.slice(0, i), ...this.stack.slice(i + 1)];
      this.changed();
    };
  }

  /** De bovenste open dialoog, of null. */
  top = (): T | null => this.stack[this.stack.length - 1] ?? null;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private changed(): void {
    for (const l of this.listeners) l();
  }
}

export const modals = new LayerStack<HTMLElement>();
