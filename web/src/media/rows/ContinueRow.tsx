// De rij Verder kijken op /media (fase 5, M7, Keuze 36), en de rij zelf die NextUpRow ook
// gebruikt. De kaarten komen van GET /media/home; een stand van een ander tabblad of apparaat
// (progressSync) werkt ze meteen bij, zonder op het opnieuw laden te wachten.
//
// Eén tabstop per rij: ← en → (en Home/End) verplaatsen de focus. Enter op een kaart kijkt
// verder (Verder kijken) of speelt af (Volgende aflevering). Shift+F10 of de menutoets (en
// rechtsklik of lang indrukken) geeft het menu van de kaart: verder kijken of afspelen, naar
// het item of de serie, en gezien markeren.

import { type JSX, type KeyboardEvent, useCallback, useRef, useState } from "react";
import { errorText, fmt, t } from "../../i18n";
import { Link } from "../../links";
import { navigate } from "../../router";
import { Icon } from "../../ui/Icon";
import { keyToMove } from "../../ui/keys";
import { type DOMHandlers, type MenuItem, useContextMenu } from "../../ui/Menu";
import { toast } from "../../ui/toast";
import { markWatched } from "../api";
import { cardHref, cardKey, episodeCode, progressFraction, resumeAt, resumeHref } from "../grid";
import { itemPath, showPath, watchPath } from "../index";
import { applyContinue, useLiveRow } from "../progressSync";
import type { Card, PlaybackProgressEvent } from "../types";

export type RowKind = "continue" | "next";

/** Waar Enter op een kaart heen gaat: verder kijken (of afspelen), anders de pagina van het item. */
export function primaryHref(card: Card): string | null {
  if (!card.node_id || card.kind === "show") return cardHref(card);
  return resumeHref(card) ?? watchPath(card.node_id);
}

/** De tekst onder de titel: waar je verder kijkt (Verder kijken) of welke aflevering. */
export function subText(card: Card, kind: RowKind): string {
  const code = card.kind === "episode" ? episodeCode(card.season, card.episode) : "";
  const at = resumeAt(card);
  const parts = [code];
  if (kind === "continue" && at !== null) parts.push(t("media.progress.resumeAt", { time: fmt.duration(at) }));
  else if (kind === "next" && !code && card.year) parts.push(String(card.year));
  return parts.filter(Boolean).join(" · ");
}

/** Wat een schermlezer bij de kaart hoort. */
export function cardLabel(card: Card, kind: RowKind): string {
  const at = resumeAt(card);
  if (kind === "continue" && at !== null) return t("media.progress.cardLabel", { title: card.title, time: fmt.duration(at) });
  const code = card.kind === "episode" ? episodeCode(card.season, card.episode) : "";
  return code ? t("media.progress.nextLabel", { title: card.title, code }) : card.title;
}

/** De items van het menu van een kaart. */
export function cardMenu(card: Card, kind: RowKind, mark: (card: Card) => void): MenuItem[] {
  const node = card.node_id;
  if (!node || card.kind === "show") return [];
  const at = resumeAt(card);
  const items: MenuItem[] = [];
  if (at !== null) {
    items.push({
      id: "resume",
      label: t("media.progress.resume", { time: fmt.duration(at) }),
      icon: "play",
      onSelect: () => navigate(watchPath(node, at)),
    });
  }
  if (kind === "next" || at === null) {
    items.push({ id: "play", label: t("media.progress.play"), icon: "play", onSelect: () => navigate(watchPath(node)) });
  }
  items.push({ id: "item", label: t("media.progress.toItem"), onSelect: () => navigate(itemPath(node)) });
  const show = card.show_id;
  if (show) items.push({ id: "show", label: t("media.progress.toShow"), onSelect: () => navigate(showPath(show)) });
  items.push({
    id: "watched",
    label: t("media.progress.markWatched"),
    icon: "check",
    separatorBefore: true,
    onSelect: () => mark(card),
  });
  return items;
}

function RowCard({
  card,
  kind,
  index,
  tabbable,
  onFocusCard,
  bind,
}: {
  card: Card;
  kind: RowKind;
  index: number;
  tabbable: boolean;
  onFocusCard(i: number): void;
  bind: DOMHandlers;
}): JSX.Element {
  const href = primaryHref(card);
  const done = progressFraction(card.progress);
  const sub = subText(card, kind);
  const body = (
    <>
      <span className="media-poster" aria-hidden="true">
        {card.poster ? <img src={card.poster} alt="" loading="lazy" decoding="async" /> : <Icon name="video" size={32} />}
        {done > 0 && (
          <span className="media-progress">
            <span style={{ transform: `scaleX(${done})` }} />
          </span>
        )}
      </span>
      <span className="media-card-title">{card.title}</span>
      {sub && <span className="media-card-sub muted">{sub}</span>}
    </>
  );
  const common = {
    className: "media-card",
    "data-index": index,
    "data-progress-card": card.node_id ?? "",
    tabIndex: tabbable ? 0 : -1,
    "aria-label": cardLabel(card, kind),
    onFocus: () => onFocusCard(index),
    ...bind,
  };
  if (!href) {
    return (
      <span {...common} aria-disabled="true">
        {body}
      </span>
    );
  }
  return (
    <Link to={href} {...common}>
      {body}
    </Link>
  );
}

/** Een rij met live kaarten (Verder kijken of Volgende aflevering). */
export function ProgressRow({
  id,
  kind,
  title,
  cards,
  apply,
}: {
  id: string;
  kind: RowKind;
  title: string;
  cards: Card[];
  apply: (cards: readonly Card[], e: PlaybackProgressEvent) => Card[];
}): JSX.Element | null {
  const shown = useLiveRow(cards, apply);
  const list = useRef<HTMLUListElement>(null);
  const [focus, setFocus] = useState(0);
  const current = Math.min(focus, Math.max(0, shown.length - 1));
  const mark = useCallback((card: Card) => {
    if (!card.node_id) return;
    markWatched({ node_ids: [card.node_id], watched: true }).then(
      () => toast(t("media.progress.marked", { title: card.title })),
      (e: unknown) => toast(errorText(e), { kind: "error" }),
    );
  }, []);
  const menu = useContextMenu<Card>((c) => cardMenu(c, kind, mark), title);
  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const move = keyToMove(e);
      if (!move || e.ctrlKey || e.metaKey) return;
      let next = current;
      if (move === "left") next = current - 1;
      else if (move === "right") next = current + 1;
      else if (move === "home") next = 0;
      else if (move === "end") next = shown.length - 1;
      else return;
      if (next < 0 || next >= shown.length || next === current) return;
      e.preventDefault();
      setFocus(next);
      const el = list.current?.querySelector<HTMLElement>(`[data-index="${next}"]`);
      el?.focus();
      el?.scrollIntoView({ block: "nearest", inline: "nearest" });
    },
    [current, shown.length],
  );
  if (shown.length === 0) return null;
  return (
    <section className="media-section" aria-labelledby={`media-row-${id}`} data-row={id}>
      <h2 id={`media-row-${id}`}>{title}</h2>
      <ul ref={list} className="media-row" onKeyDown={onKeyDown}>
        {shown.map((c, i) => (
          <li key={cardKey(c)}>
            <RowCard card={c} kind={kind} index={i} tabbable={i === current} onFocusCard={setFocus} bind={menu.bind(c)} />
          </li>
        ))}
      </ul>
      {menu.element}
    </section>
  );
}

/** Verder kijken: wat je half zag (voorbij een minuut, niet gezien), de nieuwste eerst. */
export function ContinueRow({ cards }: { cards: Card[] }): JSX.Element | null {
  return <ProgressRow id="continue" kind="continue" title={t("media.home.continue")} cards={cards} apply={applyContinue} />;
}

export default ContinueRow;
