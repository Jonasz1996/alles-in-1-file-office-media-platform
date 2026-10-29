// /media (fase 5, M2): de start van media. De bibliotheken die je ziet, en de rijen Verder kijken
// en Volgende aflevering (de data van M7, via GET /api/v1/media/home) en Net toegevoegd. Een rij
// is een lijst kaarten met één tabstop: ← en → (en Home/End) verplaatsen de focus. Zonder
// bibliotheken: uitleg en een link naar Bestanden (een map wordt een bibliotheek via het menu van
// die map).

import { type JSX, type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useReloadOn } from "../events";
import { errorText, t } from "../i18n";
import { Link } from "../links";
import { type RouteProps, useRestoreScroll } from "../router";
import { EmptyState } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { keyToMove } from "../ui/keys";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { getHome } from "./api";
import { MediaGate } from "./Gate";
import { cardKey } from "./grid";
import { libraryPath } from "./index";
import { MediaCard } from "./Library";
import { affectsHome, kindIcon } from "./library";
import type { Card, Library, MediaHome } from "./types";
import "./media.css";

/** Een rij kaarten (Verder kijken, Volgende aflevering, Net toegevoegd). */
function CardRow({ id, title, cards }: { id: string; title: string; cards: Card[] }): JSX.Element {
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

function LibraryList({ libraries }: { libraries: Library[] }): JSX.Element {
  return (
    <section className="media-section" aria-labelledby="media-libraries">
      <h2 id="media-libraries">{t("media.home.libraries")}</h2>
      <ul className="media-libraries">
        {libraries.map((l) => (
          <li key={l.id}>
            <Link to={libraryPath(l.id)} className="media-library-card">
              <span className="media-library-icon" aria-hidden="true">
                <Icon name={kindIcon(l.kind)} size={32} />
              </span>
              <span className="media-library-name">{l.name}</span>
              <span className="muted">
                {t(`media.kinds.${l.kind}`)} · {t("media.count", { n: l.item_count })}
              </span>
              {l.scan_state !== "idle" && (
                <span className={l.scan_state === "failed" ? "media-bad" : "muted"}>{t(`media.scan.${l.scan_state}`)}</span>
              )}
              {!l.mine && <span className="muted">{t("media.shared")}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Home(): JSX.Element {
  const [home, setHome] = useState<MediaHome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const gen = useRef(0);

  const load = useCallback(async () => {
    const g = ++gen.current;
    try {
      const next = await getHome();
      if (g !== gen.current) return;
      setHome(next);
      setError(null);
    } catch (e) {
      if (g === gen.current) setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);
  useReloadOn(affectsHome, () => void load(), 500);
  useRestoreScroll(home !== null);

  return (
    <main className="wide media-page">
      <PageTitle>{t("media.title")}</PageTitle>
      {error && <p role="alert">{error}</p>}
      {home === null && !error && <Skeleton lines={3} />}
      {home && home.libraries.length === 0 && (
        <EmptyState
          icon="video"
          title={t("media.home.emptyTitle")}
          action={
            <Link to="/" className="btn">
              {t("media.home.toFiles")}
            </Link>
          }
        >
          <p>{t("media.home.emptyText")}</p>
        </EmptyState>
      )}
      {home && home.continue.length > 0 && <CardRow id="continue" title={t("media.home.continue")} cards={home.continue} />}
      {home && home.next_up.length > 0 && <CardRow id="next" title={t("media.home.nextUp")} cards={home.next_up} />}
      {home && home.libraries.length > 0 && <LibraryList libraries={home.libraries} />}
      {home && home.recent.length > 0 && <CardRow id="recent" title={t("media.home.recent")} cards={home.recent} />}
    </main>
  );
}

export default function LibraryHome({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <Home />}</MediaGate>;
}
