// /media/serie/:id (fase 5, M2): een serie met haar seizoenen en afleveringen. Bovenaan één knop
// die verder gaat waar je was (of de eerste aflevering die je nog niet zag); elke aflevering is
// een link naar haar pagina, met de code (S01E02), de titel, de voortgang en "Gezien". Zonder
// seizoen (absolute nummering, Keuze 8) heet de groep "Afleveringen". media.item van de
// bibliotheek van de serie laadt de pagina opnieuw.

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { useReloadOn } from "../events";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import { type RouteProps, useRestoreScroll } from "../router";
import NotFound from "../shellPages";
import { EmptyState } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { getShow } from "./api";
import { MediaGate } from "./Gate";
import { cardHref, cardKey, episodeCode, nextEpisode, progressFraction, resumeAt } from "./grid";
import { libraryPath, watchPath } from "./index";
import { affectsLibrary } from "./library";
import type { Card, ShowDetail } from "./types";
import "./media.css";

function Episode({ card }: { card: Card }): JSX.Element {
  const href = cardHref(card);
  const done = progressFraction(card.progress);
  const code = episodeCode(card.season, card.episode);
  const text = code ? t("media.card.episode", { code, title: card.title }) : card.title;
  return (
    <li className="media-episode">
      {href ? <Link to={href}>{text}</Link> : <span>{text}</span>}
      {card.watched && <span className="muted">{t("media.card.watched")}</span>}
      {done > 0 && !card.watched && (
        <span className="muted">{t("media.card.progress", { percent: fmt.percent(done) })}</span>
      )}
    </li>
  );
}

function ShowView({ detail }: { detail: ShowDetail }): JSX.Element {
  const { show, seasons } = detail;
  const next = nextEpisode(seasons);
  const nextCode = next ? episodeCode(next.card.season, next.card.episode) || next.card.title : "";
  const at = next?.resume ? resumeAt(next.card) : null;
  const total = seasons.reduce((n, s) => n + s.episodes.length, 0);
  return (
    <main className="wide media-page media-show">
      <PageTitle
        actions={
          <Link to={libraryPath(show.library_id)} className="btn">
            {t("media.library")}
          </Link>
        }
      >
        {show.title}
      </PageTitle>
      <div className="media-item-head">
        <span className="media-poster media-item-poster" aria-hidden="true">
          {show.poster ? <img src={show.poster} alt="" decoding="async" /> : <Icon name="video" size={32} />}
        </span>
        <div className="media-item-main">
          <p className="media-meta muted">
            {[show.year ? String(show.year) : "", t("media.showPage.count", { n: total })].filter(Boolean).join(" · ")}
          </p>
          {next?.card.node_id && (
            <div className="media-actions">
              <Link to={watchPath(next.card.node_id, at ?? undefined)} className="btn primary" data-play="">
                <Icon name="play" size={16} />
                {next.resume ? t("media.showPage.resume", { code: nextCode }) : t("media.showPage.play", { code: nextCode })}
              </Link>
            </div>
          )}
          {show.overview && <p className="media-overview">{show.overview}</p>}
        </div>
      </div>
      {total === 0 && <EmptyState icon="video" title={t("media.showPage.emptyTitle")} />}
      {seasons.map((s) => {
        const id = `media-season-${s.season ?? "x"}`;
        return (
          <section key={id} className="media-section" aria-labelledby={id}>
            <h2 id={id}>{s.season === null ? t("media.showPage.episodes") : t("media.showPage.season", { n: s.season })}</h2>
            <ol className="media-episodes">
              {s.episodes.map((c) => (
                <Episode key={cardKey(c)} card={c} />
              ))}
            </ol>
          </section>
        );
      })}
    </main>
  );
}

function ShowLoader({ params }: RouteProps): JSX.Element {
  const id = params.id ?? "";
  const [detail, setDetail] = useState<ShowDetail | null>(null);
  const [error, setError] = useState<{ text: string; missing: boolean } | null>(null);
  const gen = useRef(0);

  const load = useCallback(async () => {
    const g = ++gen.current;
    try {
      const next = await getShow(id);
      if (g !== gen.current) return;
      setDetail(next);
      setError(null);
    } catch (e) {
      if (g !== gen.current) return;
      setError({ text: errorText(e), missing: e instanceof ApiError && (e.status === 404 || e.status === 422) });
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    void load();
  }, [load]);
  const libraryId = detail?.show.library_id ?? null;
  useReloadOn((e) => libraryId !== null && affectsLibrary(e, libraryId), () => void load(), 500);
  useRestoreScroll(detail !== null);

  if (error?.missing) return <NotFound params={params} />;
  if (detail) return <ShowView detail={detail} />;
  return (
    <main className="wide media-page">
      {error ? <p role="alert">{error.text}</p> : <Skeleton lines={4} />}
    </main>
  );
}

export default function ShowPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <ShowLoader params={params} />}</MediaGate>;
}
