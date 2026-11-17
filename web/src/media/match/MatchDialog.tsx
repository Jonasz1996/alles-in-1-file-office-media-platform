// Het venster Koppelen (fase 5, M8, Keuze 43): zoeken bij TMDB met posters, en kiezen. Bij het
// openen zoekt het op de titel en het jaar van het item; een eigen titel en jaar kan ook (Enter
// in het formulier). Elke kandidaat is een knop: Tab en Shift+Tab lopen erlangs, Enter of spatie
// koppelt. Een film koppel je via zijn item, een serie als geheel (de kandidaten komen dan via een
// aflevering van de serie: zie m8.md). De posters komen via ons eigen, ondertekende adres (de CSP
// blijft 'self'). Ontkoppelen zet een film terug op de gegevens uit de naam.
//
// Een fout van de API (media_provider_off, …_unavailable, …_rate, …_auth) staat in het venster;
// het blijft dan open.

import { type FormEvent, type JSX, useCallback, useEffect, useRef, useState } from "react";
import { errorText, t } from "../../i18n";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Icon } from "../../ui/Icon";
import { Skeleton } from "../../ui/Spinner";
import { toast } from "../../ui/toast";
import { candidates, matchItem, matchShow, unmatchItem } from "../api";
import type { MatchCandidate } from "../types";
import { TmdbAttribution } from "./Attribution";
import { candidateName, originalTitle, parseYear } from "./match";
import "./match.css";

export interface MatchTarget {
  /** Een film (item) of een serie (show, met een aflevering voor de kandidaten) */
  kind: "movie" | "show";
  /** Het item waarvan de kandidaten komen (bij een serie: een aflevering) */
  nodeId: string;
  /** Bij een serie: haar id */
  showId?: string;
  title: string;
  year: number | null;
  /** Er is iets van de provider: dan kan Ontkoppelen (alleen een film) */
  matched: boolean;
}

export function MatchDialog({
  target,
  onClose,
  onDone,
}: {
  target: MatchTarget;
  onClose(): void;
  /** Na koppelen of ontkoppelen (de pagina laadt opnieuw) */
  onDone(): void;
}): JSX.Element {
  const [q, setQ] = useState(target.title);
  const [year, setYear] = useState(target.year ? String(target.year) : "");
  const [found, setFound] = useState<MatchCandidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const gen = useRef(0);

  const search = useCallback(
    async (text?: string, wanted?: number | null) => {
      const g = ++gen.current;
      setSearching(true);
      setError(null);
      try {
        const list = await candidates(target.nodeId, text, wanted);
        if (g !== gen.current) return;
        setFound(list);
      } catch (e) {
        if (g !== gen.current) return;
        setFound([]);
        setError(errorText(e));
      } finally {
        if (g === gen.current) setSearching(false);
      }
    },
    [target.nodeId],
  );

  useEffect(() => {
    // Bij het openen: de titel en het jaar van het item (de API kiest ze zelf)
    void search();
    return () => {
      gen.current++;
    };
  }, [search]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const wanted = parseYear(year);
    if (wanted === "bad") {
      setError(t("media.match.problems.year"));
      return;
    }
    void search(q.trim() || undefined, wanted);
  }

  async function choose(c: MatchCandidate) {
    if (busy) return;
    setBusy(c.id);
    setError(null);
    try {
      const body = { provider: c.provider, id: c.id };
      if (target.kind === "show" && target.showId) await matchShow(target.showId, body);
      else await matchItem(target.nodeId, body);
      toast(t("media.match.chosen", { title: candidateName(c) }), { kind: "ok" });
      onClose();
      onDone();
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  async function unmatch() {
    if (busy) return;
    setBusy("unmatch");
    setError(null);
    try {
      await unmatchItem(target.nodeId);
      toast(t("media.match.unmatched"), { kind: "ok" });
      onClose();
      onDone();
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  return (
    <Dialog title={target.kind === "show" ? t("media.match.showTitle") : t("media.match.title")} size="m" onClose={onClose}>
      <form className="media-match-search" onSubmit={submit} role="search">
        <label>
          <span>{t("media.match.query")}</span>
          <input
            type="search"
            value={q}
            maxLength={300}
            autoComplete="off"
            data-autofocus=""
            data-match-query=""
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <label className="media-match-year">
          <span>{t("media.match.year")}</span>
          <input
            type="text"
            inputMode="numeric"
            value={year}
            maxLength={4}
            autoComplete="off"
            onChange={(e) => setYear(e.target.value)}
          />
        </label>
        <Button type="submit" busy={searching} data-match-search="">
          {t("media.match.search")}
        </Button>
      </form>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <section aria-labelledby="media-match-results" aria-busy={searching || undefined}>
        <h3 id="media-match-results" className="sr-only">
          {t("media.match.results")}
        </h3>
        {found === null && <Skeleton lines={3} />}
        {found !== null && found.length === 0 && !error && !searching && <p className="muted">{t("media.match.none")}</p>}
        {found !== null && found.length > 0 && (
          <ul className="media-match-list">
            {found.map((c) => {
              const original = originalTitle(c);
              return (
                <li key={`${c.provider}:${c.id}`}>
                  <button
                    type="button"
                    className="media-match-candidate"
                    data-candidate={c.id}
                    aria-label={t("media.match.choose", { title: candidateName(c) })}
                    aria-busy={busy === c.id || undefined}
                    disabled={busy !== null && busy !== c.id}
                    onClick={() => void choose(c)}
                  >
                    <span className="media-match-poster" aria-hidden="true">
                      {c.poster ? <img src={c.poster} alt="" decoding="async" /> : <Icon name="video" size={24} />}
                    </span>
                    <span className="media-match-text">
                      <strong>{candidateName(c)}</strong>
                      {original && <small className="muted">{t("media.match.original", { title: original })}</small>}
                      {c.overview && <small className="media-match-overview">{c.overview}</small>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      <TmdbAttribution />
      <div className="dialog-actions">
        {target.kind === "movie" && target.matched && (
          <Button variant="danger" busy={busy === "unmatch"} onClick={() => void unmatch()} data-unmatch="">
            {t("media.match.unmatch")}
          </Button>
        )}
        <Button onClick={onClose}>{t("common.cancel")}</Button>
      </div>
    </Dialog>
  );
}

export default MatchDialog;
