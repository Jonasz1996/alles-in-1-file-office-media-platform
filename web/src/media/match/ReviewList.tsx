// De lijst "Nakijken" op de startpagina van een bibliotheek (fase 5, M8, Keuze 42): de films,
// series en afleveringen waar het opzoeken geen zekere match vond. Elke regel is een link naar het
// item of de serie (daar staat Koppelen). Alleen in een bibliotheek met online opzoeken, en alleen
// als er iets na te kijken is (de API geeft een lege lijst aan wie niet mag bewerken). media.item
// en media.library van deze bibliotheek laden de lijst opnieuw.

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { useReloadOn } from "../../events";
import { errorText, t } from "../../i18n";
import { Link } from "../../links";
import { Button } from "../../ui/Button";
import { cardHref, cardKey, episodeCode, mergePage } from "../grid";
import { affectsLibrary } from "../library";
import type { Card, Library } from "../types";
import { REVIEW_LIMIT, reviewList } from "./match";
import "./match.css";

/** "Titel (1999)" of "Titel · S01E02". */
export function reviewText(c: Card): string {
  const code = c.kind === "episode" ? episodeCode(c.season, c.episode) : "";
  if (code) return `${c.title} · ${code}`;
  return c.year ? `${c.title} (${c.year})` : c.title;
}

/** Is er hier een lijst Nakijken? Alleen films en series, met online opzoeken. */
export function hasReview(lib: Pick<Library, "kind" | "online">): boolean {
  return lib.online && (lib.kind === "movies" || lib.kind === "shows");
}

export function ReviewList({ lib }: { lib: Library }): JSX.Element | null {
  const [cards, setCards] = useState<Card[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const gen = useRef(0);

  const load = useCallback(async () => {
    const g = ++gen.current;
    try {
      const page = await reviewList(lib.id);
      if (g !== gen.current) return;
      setCards(page.items);
      setNext(page.next);
      setError(null);
    } catch (e) {
      if (g === gen.current) setError(errorText(e));
    }
  }, [lib.id]);

  useEffect(() => {
    void load();
  }, [load]);
  useReloadOn((e) => affectsLibrary(e, lib.id), () => void load(), 1000);

  async function more() {
    if (!next || busy) return;
    const g = gen.current;
    setBusy(true);
    try {
      const page = await reviewList(lib.id, next);
      if (g !== gen.current) return;
      setCards((old) => mergePage(old, page));
      setNext(page.next);
    } catch (e) {
      if (g === gen.current) setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (cards.length === 0 && !error) return null;
  const count = next ? `${cards.length}+` : String(cards.length);
  return (
    <section className="media-section media-review" aria-labelledby="media-review">
      <h2 id="media-review">
        {t("media.match.review")} <span className="muted">({count})</span>
      </h2>
      <p className="muted">{t("media.match.reviewHint")}</p>
      {error && <p role="alert">{error}</p>}
      <ul className="media-list media-review-list">
        {cards.map((c) => {
          const href = cardHref(c);
          return (
            <li key={cardKey(c)} data-review="">
              {href ? <Link to={href}>{reviewText(c)}</Link> : <span>{reviewText(c)}</span>}
            </li>
          );
        })}
      </ul>
      {next && cards.length >= REVIEW_LIMIT && (
        <Button busy={busy} onClick={() => void more()}>
          {t("media.match.reviewMore")}
        </Button>
      )}
    </section>
  );
}
