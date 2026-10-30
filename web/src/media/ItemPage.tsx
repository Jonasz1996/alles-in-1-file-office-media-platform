// /media/item/:node (fase 5, M2): een film, aflevering of video. Afspelen (/kijken/:node), en
// "Verder op 12:34" als je er half doorheen bent; de delen van een film in delen, de
// ondertitels, de sporen, de hoofdstukken (elk een link die daar begint) en de staat van de
// analyse. Een probe die mislukte, geeft de badge "Kan niet afspelen" met de reden (Keuze 46).
// media.item en playback.progress van dit item laden de pagina opnieuw (de stand van kijken).

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { useReloadOn } from "../events";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import type { RouteProps } from "../router";
import NotFound from "../shellPages";
import { Icon } from "../ui/Icon";
import { PageTitle } from "../ui/PageTitle";
import { Skeleton } from "../ui/Spinner";
import { getItem } from "./api";
import { MediaGate } from "./Gate";
import { episodeCode, progressFraction, resumeAt } from "./grid";
import { libraryPath, showPath, watchPath } from "./index";
import { affectsItem, analysisRows, extraKey, streamText } from "./library";
import type { Item } from "./types";
import "./media.css";

function ItemView({ item }: { item: Item }): JSX.Element {
  const at = resumeAt(item);
  const done = progressFraction(item.progress);
  const code = item.kind === "episode" ? episodeCode(item.season, item.episode) : "";
  const facts = [
    code,
    item.year ? String(item.year) : "",
    item.duration_ms ? fmt.duration(item.duration_ms / 1000) : "",
    item.extra ? t(`media.itemPage.extras.${extraKey(item.extra)}`) : "",
    item.watched ? t("media.card.watched") : "",
  ].filter(Boolean);
  const analysis = analysisRows(item.analysis);
  return (
    <main className="wide media-page media-item">
      <PageTitle
        actions={
          <Link to={libraryPath(item.library_id)} className="btn">
            {t("media.library")}
          </Link>
        }
      >
        {item.title}
      </PageTitle>
      <div className="media-item-head">
        <span className="media-poster media-item-poster" aria-hidden="true">
          {item.poster ? <img src={item.poster} alt="" decoding="async" /> : <Icon name="video" size={32} />}
        </span>
        <div className="media-item-main">
          {facts.length > 0 && <p className="media-meta muted">{facts.join(" · ")}</p>}
          {item.show_id && (
            <p>
              <Link to={showPath(item.show_id)}>{t("media.itemPage.toShow")}</Link>
            </p>
          )}
          {item.error && (
            <p className="media-badge media-bad" role="status">
              {t("media.itemPage.cannotPlayReason", { reason: item.error })}
            </p>
          )}
          <div className="media-actions">
            <Link to={watchPath(item.node_id)} className="btn primary" data-play="">
              <Icon name="play" size={16} />
              {t("media.itemPage.play")}
            </Link>
            {at !== null && (
              <Link to={watchPath(item.node_id, at)} className="btn" data-resume="">
                {t("media.itemPage.resume", { time: fmt.duration(at) })}
              </Link>
            )}
          </div>
          {done > 0 && (
            <span className="media-progress media-item-progress" aria-hidden="true">
              <span style={{ transform: `scaleX(${done})` }} />
            </span>
          )}
          {item.overview && <p className="media-overview">{item.overview}</p>}
        </div>
      </div>

      {item.parts.length > 1 && (
        <section className="media-section" aria-labelledby="media-parts">
          <h2 id="media-parts">{t("media.itemPage.parts")}</h2>
          <ul className="media-list">
            {item.parts.map((p) => (
              <li key={p.node_id}>
                <Link to={watchPath(p.node_id)}>{t("media.itemPage.part", { n: p.part })}</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {item.chapters.length > 0 && (
        <section className="media-section" aria-labelledby="media-chapters">
          <h2 id="media-chapters">{t("media.itemPage.chapters")}</h2>
          <ol className="media-list">
            {item.chapters.map((c, i) => (
              <li key={c.start_ms}>
                <Link to={watchPath(item.node_id, Math.floor(c.start_ms / 1000))}>
                  {c.title || t("media.itemPage.chapter", { n: i + 1 })}
                </Link>{" "}
                <span className="muted">{fmt.duration(c.start_ms / 1000)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className="media-section" aria-labelledby="media-details">
        <h2 id="media-details">{t("media.itemPage.details")}</h2>
        <dl className="media-facts">
          {item.duration_ms ? (
            <>
              <dt>{t("media.itemPage.duration")}</dt>
              <dd>{fmt.duration(item.duration_ms / 1000)}</dd>
            </>
          ) : null}
          {item.streams.length > 0 && (
            <>
              <dt>{t("media.itemPage.streams")}</dt>
              <dd>
                <ul className="media-list">
                  {item.streams.map((s) => (
                    <li key={s.index}>
                      {t(`media.itemPage.streamKinds.${s.type}`)}: {streamText(s)}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
          {item.subtitles.length > 0 && (
            <>
              <dt>{t("media.itemPage.subtitles")}</dt>
              <dd>
                <ul className="media-list">
                  {item.subtitles.map((s) => (
                    <li key={s.id}>
                      {[
                        s.label,
                        s.forced ? t("media.itemPage.forced") : "",
                        s.sdh ? t("media.itemPage.sdh") : "",
                        s.kind === "image" ? t("media.itemPage.imageSub") : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
          {analysis.length > 0 && (
            <>
              <dt>{t("media.itemPage.analysis")}</dt>
              <dd>
                <ul className="media-list">
                  {analysis.map(([step, state]) => (
                    <li key={step} data-step={step} data-state={state}>
                      {t(`media.itemPage.steps.${step}`)}: {t(`media.itemPage.states.${state}`)}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
          <dt>{t("media.itemPage.match")}</dt>
          <dd>{t(`media.itemPage.matchStates.${item.match_state}`)}</dd>
        </dl>
      </section>
    </main>
  );
}

function ItemLoader({ params }: RouteProps): JSX.Element {
  const node = params.node ?? "";
  const [item, setItem] = useState<Item | null>(null);
  const [error, setError] = useState<{ text: string; missing: boolean } | null>(null);
  const gen = useRef(0);

  const load = useCallback(async () => {
    const g = ++gen.current;
    try {
      const next = await getItem(node);
      if (g !== gen.current) return;
      setItem(next);
      setError(null);
    } catch (e) {
      if (g !== gen.current) return;
      const missing = e instanceof ApiError && (e.status === 404 || e.status === 422);
      setError({ text: errorText(e), missing });
    }
  }, [node]);

  useEffect(() => {
    setItem(null);
    setError(null);
    void load();
  }, [load]);
  useReloadOn((e) => affectsItem(e, node), () => void load(), 500);

  if (error?.missing) return <NotFound params={params} />;
  if (item) return <ItemView item={item} />;
  return (
    <main className="wide media-page">
      {error ? <p role="alert">{error.text}</p> : <Skeleton lines={4} />}
    </main>
  );
}

export default function ItemPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <ItemLoader params={params} />}</MediaGate>;
}
