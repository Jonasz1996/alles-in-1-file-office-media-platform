// Muziek → YouTube (fixlijst 26-11, punt 7): zoeken op YouTube, kijken (ook live) in de speler van
// YouTube zonder cookies, en een video als mp3 bewaren in een muziekmap. Dat laatste is een taak
// op de server (api/app/youtube/service.py); staat de map in een muziekbibliotheek, dan is het
// nummer na afloop meteen geïndexeerd. De gebeurtenis youtube.mp3 zegt wanneer het klaar is.
// Daarnaast: als video (mp4) bewaren, sponsorstukken eruit knippen (SponsorBlock) en abonnementen
// op kanalen zonder Google-account, met per kanaal automatisch bewaren (api/app/youtube/subs.py).
import {
  type FormEvent,
  type JSX,
  useCallback,
  useEffect,
  useState,
} from "react";
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
  channel_id?: string | null;
  duration?: number | null;
  live?: boolean;
  published?: string;
}

interface Sub {
  channel: string;
  title: string;
  auto: "off" | "mp3" | "mp4";
  folder: string | null;
  sponsor: boolean;
}

interface Conversion {
  job: string;
  video: string;
  title: string;
  kind: "mp3" | "mp4";
  state: "queued" | "running" | "done" | "failed";
  error: string | null;
  created_at: string;
}

interface Target {
  id: string;
  name: string;
}

export function duration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(Math.floor(s % 60)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export default function YouTubePanel({
  libs,
}: {
  libs: Library[];
}): JSX.Element {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[] | null>(null);
  const [playing, setPlaying] = useState<Result | null>(null);
  const [target, setTarget] = useState<Target | null>(
    libs[0] ? { id: libs[0].root_id, name: libs[0].name } : null,
  );
  const [picking, setPicking] = useState(false);
  const [mine, setMine] = useState<Conversion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"search" | "subs">("search");
  const [sponsor, setSponsor] = useState(false);
  const [subs, setSubs] = useState<Sub[]>([]);
  const [feed, setFeed] = useState<Result[] | null>(null);
  const [channel, setChannel] = useState("");

  const loadSubs = useCallback(async () => {
    setSubs(await api.request<Sub[]>("GET", `${BASE}/abonnementen`));
    setFeed(await api.request<Result[]>("GET", `${BASE}/feed`));
  }, []);

  useEffect(() => {
    if (view === "subs" && enabled)
      loadSubs().catch((e) => setError(errorText(e)));
  }, [view, enabled, loadSubs]);

  async function act(label: string, run: () => Promise<unknown>) {
    setBusy(label);
    try {
      await run();
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  function subscribe(ref: string) {
    void act(`sub:${ref}`, async () => {
      const sub = await api.request<Sub>("POST", `${BASE}/abonnementen`, {
        channel: ref,
      });
      toast(t("music.youtube.subscribed", { title: sub.title }), {
        kind: "ok",
      });
      setChannel("");
      if (view === "subs") await loadSubs();
    });
  }

  function update(sub: Sub, change: Partial<Sub>) {
    const next = { ...sub, ...change };
    if (next.auto !== "off" && !next.folder) next.folder = target?.id ?? null;
    void act(`set:${sub.channel}`, async () => {
      await api.request("PUT", `${BASE}/abonnementen/${sub.channel}`, {
        auto: next.auto,
        folder: next.folder,
        sponsor: next.sponsor,
      });
      await loadSubs();
    });
  }

  function unsubscribe(sub: Sub) {
    void act(`del:${sub.channel}`, async () => {
      await api.request("DELETE", `${BASE}/abonnementen/${sub.channel}`);
      await loadSubs();
    });
  }

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
    if (e.data.state === "done")
      toast(t("music.youtube.done", { title }), { kind: "ok" });
    else if (e.data.state === "failed")
      toast(t("music.youtube.failed", { title }), { kind: "error" });
    reload().catch(() => {});
  });

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setBusy("search");
    try {
      setResults(
        await api.request<Result[]>(
          "GET",
          `${BASE}/zoek?q=${encodeURIComponent(query.trim())}`,
        ),
      );
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function save(r: Result, kind: "mp3" | "mp4") {
    if (!target) return;
    setBusy(`${kind}:${r.id}`);
    try {
      await api.request("POST", `${BASE}/mp3`, {
        video: r.id,
        folder: target.id,
        title: r.title,
        kind,
        sponsor,
      });
      toast(
        t(
          kind === "mp3" ? "music.youtube.queued" : "music.youtube.queuedVideo",
          { title: r.title, folder: target.name },
        ),
        {
          kind: "ok",
        },
      );
      setError(null);
      await reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  function row(r: Result): JSX.Element {
    const meta = r.live
      ? ` · ${t("music.youtube.live")}`
      : r.duration
        ? ` · ${duration(r.duration)}`
        : r.published
          ? ` · ${new Date(r.published).toLocaleDateString()}`
          : "";
    return (
      <li key={r.id}>
        <img
          src={`https://i.ytimg.com/vi/${r.id}/mqdefault.jpg`}
          alt=""
          loading="lazy"
          width={160}
          height={90}
        />
        <div className="youtube-info">
          <strong>{r.title}</strong>
          <span className="muted">
            {r.channel}
            {meta}
          </span>
          <span className="toolbar">
            <Button onClick={() => setPlaying(r)}>
              {r.live ? t("music.youtube.watchLive") : t("music.youtube.play")}
            </Button>
            {!r.live && (
              <>
                <Button
                  busy={busy === `mp3:${r.id}`}
                  disabled={!target}
                  onClick={() => void save(r, "mp3")}
                >
                  {t("music.youtube.save")}
                </Button>
                <Button
                  busy={busy === `mp4:${r.id}`}
                  disabled={!target}
                  onClick={() => void save(r, "mp4")}
                >
                  {t("music.youtube.saveVideo")}
                </Button>
              </>
            )}
            {view === "search" &&
              r.channel_id &&
              !subs.some((x) => x.channel === r.channel_id) && (
                <Button
                  busy={busy === `sub:${r.channel_id}`}
                  onClick={() => subscribe(r.channel_id as string)}
                >
                  {t("music.youtube.subscribe")}
                </Button>
              )}
          </span>
        </div>
      </li>
    );
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
      <div
        className="toolbar"
        role="group"
        aria-label={t("music.youtube.views")}
      >
        <Button
          aria-pressed={view === "search"}
          onClick={() => setView("search")}
        >
          {t("music.youtube.viewSearch")}
        </Button>
        <Button aria-pressed={view === "subs"} onClick={() => setView("subs")}>
          {t("music.youtube.viewSubs")}
        </Button>
      </div>
      {view === "search" && (
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
      )}
      <div className="music-toolbar">
        <span>
          {t("music.youtube.folder")}:{" "}
          <strong>{target ? target.name : t("music.youtube.noFolder")}</strong>
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
        <label className="checkbox">
          <input
            type="checkbox"
            checked={sponsor}
            onChange={(e) => setSponsor(e.target.checked)}
          />
          {t("music.youtube.sponsor")}
        </label>
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
            <Button onClick={() => setPlaying(null)}>
              {t("music.youtube.close")}
            </Button>
          </p>
        </section>
      )}

      {view === "search" && results !== null && results.length === 0 && (
        <p className="muted">{t("music.youtube.none")}</p>
      )}
      {view === "search" && results && results.length > 0 && (
        <ul className="youtube-results" aria-label={t("music.youtube.results")}>
          {results.map(row)}
        </ul>
      )}

      {view === "subs" && (
        <section aria-labelledby="youtube-subs">
          <h2 id="youtube-subs">{t("music.youtube.subs")}</h2>
          <form
            className="music-toolbar"
            onSubmit={(e) => {
              e.preventDefault();
              if (channel.trim()) subscribe(channel.trim());
            }}
          >
            <label>
              <span>{t("music.youtube.addChannel")}</span>
              <input
                name="youtube-channel"
                value={channel}
                maxLength={300}
                placeholder={t("music.youtube.channelPlaceholder")}
                onChange={(e) => setChannel(e.target.value)}
              />
            </label>
            <Button type="submit" busy={busy === `sub:${channel.trim()}`}>
              {t("music.youtube.subscribe")}
            </Button>
          </form>
          {subs.length === 0 && (
            <p className="muted">{t("music.youtube.noSubs")}</p>
          )}
          <ul className="youtube-subs">
            {subs.map((sub) => (
              <li key={sub.channel}>
                <strong>{sub.title}</strong>
                <label>
                  <span>{t("music.youtube.auto")}</span>
                  <select
                    value={sub.auto}
                    disabled={busy === `set:${sub.channel}`}
                    onChange={(e) =>
                      update(sub, { auto: e.target.value as Sub["auto"] })
                    }
                  >
                    <option value="off">{t("music.youtube.autoOff")}</option>
                    <option value="mp3">{t("music.youtube.autoMp3")}</option>
                    <option value="mp4">{t("music.youtube.autoMp4")}</option>
                  </select>
                </label>
                {sub.auto !== "off" && (
                  <>
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={sub.sponsor}
                        onChange={(e) =>
                          update(sub, { sponsor: e.target.checked })
                        }
                      />
                      {t("music.youtube.sponsor")}
                    </label>
                    <Button
                      disabled={!target}
                      onClick={() =>
                        update(sub, { folder: target?.id ?? null })
                      }
                    >
                      {t("music.youtube.useFolder", {
                        folder: target?.name ?? "",
                      })}
                    </Button>
                  </>
                )}
                <Button
                  busy={busy === `del:${sub.channel}`}
                  onClick={() => unsubscribe(sub)}
                >
                  {t("music.youtube.unsubscribe")}
                </Button>
              </li>
            ))}
          </ul>
          {feed && feed.length > 0 && (
            <>
              <h2>{t("music.youtube.feed")}</h2>
              <ul
                className="youtube-results"
                aria-label={t("music.youtube.feed")}
              >
                {feed.map(row)}
              </ul>
            </>
          )}
        </section>
      )}

      {mine.length > 0 && (
        <section aria-labelledby="youtube-mine">
          <h2 id="youtube-mine">{t("music.youtube.mine")}</h2>
          <ul className="youtube-mine">
            {mine.map((c) => (
              <li key={c.job}>
                {c.title || c.video} ({c.kind}) ·{" "}
                {t(`music.youtube.state.${c.state}`)}
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
