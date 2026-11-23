// Een rij kaarten met één tabstop: ← en → (en Home/End) verplaatsen de focus. Voor Net
// toegevoegd en de andere rijen op de start, en "Ook in deze collectie" op de filmpagina.

import { type JSX, type KeyboardEvent, useCallback, useRef, useState } from "react";
import { keyToMove } from "../../ui/keys";
import { cardKey } from "../grid";
import { MediaCard } from "../Library";
import type { Card } from "../types";

/** Een rij kaarten. */
export function CardRow({ id, title, cards }: { id: string; title: string; cards: Card[] }): JSX.Element {
  const list = useRef<HTMLUListElement>(null);
  const [focus, setFocus] = useState(0);
  const current = Math.min(focus, Math.max(0, cards.length - 1));
  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const move = keyToMove(e);
      if (!move || e.ctrlKey || e.metaKey) return;
      let next = current;
      if (move === "left") next = current - 1;
      else if (move === "right") next = current + 1;
      else if (move === "home") next = 0;
      else if (move === "end") next = cards.length - 1;
      else return;
      if (next < 0 || next >= cards.length || next === current) return;
      e.preventDefault();
      setFocus(next);
      const el = list.current?.querySelector<HTMLElement>(`[data-index="${next}"]`);
      el?.focus();
      el?.scrollIntoView({ block: "nearest", inline: "nearest" });
    },
    [current, cards.length],
  );
  return (
    <section className="media-section" aria-labelledby={`media-row-${id}`}>
      <h2 id={`media-row-${id}`}>{title}</h2>
      <ul ref={list} className="media-row" onKeyDown={onKeyDown}>
        {cards.map((c, i) => (
          <li key={cardKey(c)}>
            <MediaCard card={c} index={i} tabbable={i === current} onFocusCard={setFocus} />
          </li>
        ))}
      </ul>
    </section>
  );
}
