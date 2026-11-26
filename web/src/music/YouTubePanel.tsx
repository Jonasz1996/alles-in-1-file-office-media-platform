// Muziek → YouTube (fixlijst 26-11, punt 7): zoeken op YouTube, kijken (ook live) in de speler van
// YouTube zonder cookies, en een video als mp3 bewaren in een muziekmap. Dat laatste is een taak
// op de server (api/app/youtube/service.py); staat de map in een muziekbibliotheek, dan is het
// nummer na afloop meteen geïndexeerd. De gebeurtenis youtube.mp3 zegt wanneer het klaar is.
import { type FormEvent, type JSX, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useServerEvents } from "../events";
import { FolderPicker } from "../FolderPicker";
import { errorText, loadAreas, t } from "../i18n";
import type { Library } from "../media/types";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { toast } from "../ui/toast";

const BASE = "/api/v1/youtube";

interface Result {
  id: string;
  title: string;
  channel: string;
  duration: number | null;
  live: boolean;
}

interface Conversion {
  job: string;
  video: string;
  title: string;
  state: "queued" | "running" | "done" | "failed";
  error: string | null;
  created_at: string;
}

interface Target {
  id: string;
  name: string;
}

export function duration(s: number | null): string {
  if (s === null) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(Math.floor(s % 60)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export default function YouTubePanel({ libs }: { libs: Library[] }): JSX.Element {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[] | null>(null);
  const [playing, setPlaying] = useState<Result | null>(null);
  const [target, setTarget] = useState<Target | null>(libs[0] ? { id: libs[0].root_id, name: libs[0].name } : null);
  const [picking, setPicking] = useState(false);
  const [mine, setMine] = useState<Conversion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setMine(await api.request<Conversion[]>("GET", `${BASE}/mp3`));
  }, []);

  useEffect(() => {
    api
      .request<{ enabled: boolean }>("GET", BASE)
      .then((s) => {
        setEnabled(s.enabled);
        if (s.enabled) return reload();
      })
      .catch((e) => setError(errorText(e)));
  }, [reload]);

  useServerEvents((e) => {
    if (e.type !== "youtube.mp3") return;
    const title = String(e.data.title ?? "");
    if (e.data.state === "done") toast(t("music.youtube.done", { title }), { kind: "ok" });
    else if (e.data.state === "failed") toast(t("music.youtube.failed", { title }), { kind: "error" });
    reload().catch(() => {});
  });

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setBusy("search");
    try {
      setResults(await api.request<Result[]>("GET", `${BASE}/zoek?q=${encodeURIComponent(query.trim())}`));
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function save(r: Result) {
    if (!target) return;
    setBusy(r.id);
    try {
      await api.request("POST", `${BASE}/mp3`, { video: r.id, folder: target.id, title: r.title });
      toast(t("music.youtube.queued", { title: r.title, folder: target.name }), { kind: "ok" });
      setError(null);
      await reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  if (enabled === false) {
    return (
      <EmptyState icon="audio" title={t("music.youtube.off.title")}>
        <p>{t("music.youtube.off.text")}</p>
      </EmptyState>
    );
  }

  return (
    <div className="youtube" data-youtube="">
      <form className="music-toolbar" onSubmit={search} role="search">
        <label>
          <span>{t("music.youtube.search")}</span>
          <input
            type="search"
            name="youtube-q"
            value={query}
            maxLength={200}
            placeholder={t("music.youtube.placeholder")}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <Button type="submit" variant="primary" busy={busy === "search"}>
          {t("music.youtube.go")}
        </Button>
      </form>
      <div className="music-toolbar">
        <span>
          {t("music.youtube.folder")}: <strong>{target ? target.name : t("music.youtube.noFolder")}</strong>
        </span>
        <Button
          onClick={() => {
            loadAreas(["browser"])
              .then(() => setPicking(true))
              .catch((e) => setError(errorText(e)));
          }}
        >
          {t("music.youtube.pick")}
        </Button>
        {libs.length > 1 && (
          <select
            aria-label={t("music.library")}
            value={libs.find((l) => l.root_id === target?.id)?.id ?? ""}
            onChange={(e) => {
              const lib = libs.find((l) => l.id === e.target.value);
              if (lib) setTarget({ id: lib.root_id, name: lib.name });
            }}
          >
            <option value="" disabled>
              {t("music.library")}
            </option>
            {libs.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        )}
      </div>
      {error && (
        <p role="alert" className="music-error">
          {error}
        </p>
      )}

      {playing && (
        <section className="youtube-player" aria-label={playing.title}>
          <div className="youtube-frame">
            <iframe
              title={playing.title}
              src={`https://www.youtube-nocookie.com/embed/${playing.id}?autoplay=1&rel=0`}
              allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
            />
          </div>
          <p>
            <strong>{playing.title}</strong> · {playing.channel}{" "}
            <Button onClick={() => setPlaying(null)}>{t("music.youtube.close")}</Button>
          </p>
        </section>
      )}

      {results !== null && results.length === 0 && <p className="muted">{t("music.youtube.none")}</p>}
      {results && results.length > 0 && (
        <ul className="youtube-results" aria-label={t("music.youtube.results")}>
          {results.map((r) => (
            <li key={r.id}>
              <img src={`https://i.ytimg.com/vi/${r.id}/mqdefault.jpg`} alt="" loading="lazy" width={160} height={90} />
              <div className="youtube-info">
                <strong>{r.title}</strong>
                <span className="muted">
                  {r.channel}
                  {r.live ? ` · ${t("music.youtube.live")}` : r.duration !== null ? ` · ${duration(r.duration)}` : ""}
                </span>
                <span className="toolbar">
                  <Button onClick={() => setPlaying(r)}>
                    {r.live ? t("music.youtube.watchLive") : t("music.youtube.play")}
                  </Button>
                  {!r.live && (
                    <Button busy={busy === r.id} disabled={!target} onClick={() => void save(r)}>
                      {t("music.youtube.save")}
                    </Button>
                  )}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {mine.length > 0 && (
        <section aria-labelledby="youtube-mine">
          <h2 id="youtube-mine">{t("music.youtube.mine")}</h2>
          <ul className="youtube-mine">
            {mine.map((c) => (
              <li key={c.job}>
                {c.title || c.video} · {t(`music.youtube.state.${c.state}`)}
                {c.state === "failed" && c.error ? ` (${c.error})` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {picking && (
        <FolderPicker
          title={t("music.youtube.pickTitle")}
          action={t("music.youtube.pickAction")}
          startId={target?.id ?? null}
          onPick={(id, folder) => {
            setTarget({ id, name: folder.name });
            setPicking(false);
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  );
}
