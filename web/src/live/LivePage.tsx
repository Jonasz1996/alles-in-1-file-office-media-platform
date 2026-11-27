// /live en /live/:id: live tv (IPTV). Links (of boven, op een smal scherm) de speler met wat er
// nu en straks is; daaronder de kanalen (zoeken, groep, alleen favorieten) of de gids (een
// raster van 6 uur, met Eerder/Later/Nu). Een kanaal kiezen zet het in het adres, zodat Terug
// en een bladwijzer werken. Op de tv (webOS) en met het toetsenbord: Kanaal+/Kanaal- (PageUp en
// PageDown) en de pijltjes (de tv-stand, tv/tv.ts) bedienen alles.
//
// De speler zelf is ./engine.ts (fMP4 van de server in MediaSource). Een kanaal dat niet speelt
// zegt waarom: alle tuners bezet (met de kanalen die lopen), de aanbieder weigert, of de browser
// kan het niet.
import { type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isGuest, useCurrentUser } from "../currentUser";
import { errorText, fmt, t } from "../i18n";
import { Link } from "../links";
import { MediaGate } from "../media/Gate";
import { navigate, type RouteProps } from "../router";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";
import { type Channel, type Channels, type Guide, getChannels, getGuide, getProgramme, type Programme, setFavorite, type Slot } from "./api";
import { LiveEngine, type LiveState } from "./engine";
import "./live.css";

const GUIDE_HOURS = 6;
const SLOT_MS = 30 * 60 * 1000;

function progress(slot: Slot | null, now: number): number {
  if (!slot) return 0;
  const a = Date.parse(slot.start);
  const b = Date.parse(slot.stop);
  return b > a ? Math.min(100, Math.max(0, ((now - a) / (b - a)) * 100)) : 0;
}

function slotText(slot: Slot): string {
  const extra = [slot.subtitle, slot.episode].filter(Boolean).join(" · ");
  return extra ? `${slot.title} (${extra})` : slot.title;
}

function stateText(state: LiveState, name: string): string | null {
  switch (state.kind) {
    case "connecting":
      return t("live.state.connecting", { name });
    case "retrying":
      return t("live.state.retrying");
    case "ended":
      return t("live.state.ended");
    case "unsupported":
      return t("live.state.unsupported");
    case "busy":
      return t("live.state.busy", { channels: state.channels.join(", ") || "?" });
    case "failed":
      return t("live.state.failed", { reason: state.reason });
    default:
      return null;
  }
}

function Logo({ channel }: { channel: Channel }): JSX.Element {
  return channel.logo ? (
    <img className="live-logo" src={channel.logo} alt="" loading="lazy" decoding="async" />
  ) : (
    <span className="live-logo live-logo-empty" aria-hidden="true">
      {channel.name.slice(0, 2)}
    </span>
  );
}

function Player({ channel, onStep }: { channel: Channel | null; onStep: (delta: number) => void }): JSX.Element {
  const video = useRef<HTMLVideoElement>(null);
  const engine = useRef<LiveEngine | null>(null);
  const [state, setState] = useState<LiveState>({ kind: "idle" });

  useEffect(() => {
    if (!video.current) return;
    const e = new LiveEngine(video.current, setState);
    engine.current = e;
    return () => e.stop();
  }, []);

  useEffect(() => {
    if (!engine.current) return;
    if (channel) engine.current.play(channel.id);
    else engine.current.stop();
  }, [channel?.id]); // alleen bij een ander kanaal opnieuw beginnen

  const message = channel ? stateText(state, channel.name) : t("live.pick");
  const failed = state.kind === "busy" || state.kind === "failed" || state.kind === "ended";
  return (
    <div className="live-player" data-live-state={state.kind}>
      <video ref={video} className="live-video" controls playsInline autoPlay />
      {message && (
        <p className={failed ? "live-message error" : "live-message muted"} role="status">
          {message}
        </p>
      )}
      <div className="toolbar">
        <Button onClick={() => onStep(-1)} disabled={!channel}>
          {t("live.channelDown")}
        </Button>
        <Button onClick={() => onStep(1)} disabled={!channel}>
          {t("live.channelUp")}
        </Button>
        {failed && channel && <Button onClick={() => engine.current?.play(channel.id)}>{t("live.again")}</Button>}
        <Button
          disabled={!channel}
          onClick={() => {
            const v = video.current;
            if (v && document.fullscreenEnabled) void v.requestFullscreen().catch(() => {});
          }}
        >
          {t("live.fullscreen")}
        </Button>
      </div>
      {channel && (
        <div className="live-now">
          <h2>
            {channel.number !== null && <span className="muted">{channel.number} </span>}
            {channel.name}
          </h2>
          {channel.now ? (
            <p>
              <strong>{t("live.now")}:</strong> {fmt.time(channel.now.start)}–{fmt.time(channel.now.stop)}{" "}
              {slotText(channel.now)}
            </p>
          ) : (
            <p className="muted">{t("live.noGuide")}</p>
          )}
          {channel.next && (
            <p className="muted">
              <strong>{t("live.next")}:</strong> {fmt.time(channel.next.start)} {slotText(channel.next)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ChannelList({
  channels,
  current,
  now,
  onFavorite,
}: {
  channels: Channel[];
  current: string | null;
  now: number;
  onFavorite: (c: Channel) => void;
}): JSX.Element {
  return (
    <ul className="live-channels" data-live="channels">
      {channels.map((c) => (
        <li key={c.id} className={c.id === current ? "live-channel current" : "live-channel"}>
          <Link to={`/live/${c.id}`} replace className="live-channel-link" aria-current={c.id === current ? "true" : undefined}>
            <Logo channel={c} />
            <span className="live-channel-text">
              <span className="live-channel-name">
                {c.number !== null && <span className="muted">{c.number} </span>}
                {c.name}
              </span>
              {c.now && <span className="live-channel-now muted">{c.now.title}</span>}
              {c.now && (
                <span className="live-bar" aria-hidden="true">
                  <span style={{ width: `${progress(c.now, now)}%` }} />
                </span>
              )}
            </span>
          </Link>
          <button
            type="button"
            className={c.favorite ? "live-fav on" : "live-fav"}
            aria-pressed={c.favorite}
            aria-label={c.favorite ? t("live.unfavorite") : t("live.favorite")}
            title={c.favorite ? t("live.unfavorite") : t("live.favorite")}
            onClick={() => onFavorite(c)}
          >
            {c.favorite ? "★" : "☆"}
          </button>
        </li>
      ))}
    </ul>
  );
}

function GuideGrid({ channels, current }: { channels: Channel[]; current: string | null }): JSX.Element {
  const [start, setStart] = useState(() => new Date(Math.floor(Date.now() / SLOT_MS) * SLOT_MS));
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ programme: Programme; channel: Channel } | null>(null);

  useEffect(() => {
    let live = true;
    getGuide(start, GUIDE_HOURS)
      .then((g) => live && setGuide(g))
      .catch((e) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [start]);

  const from = guide ? Date.parse(guide.start) : start.getTime();
  const to = guide ? Date.parse(guide.stop) : from + GUIDE_HOURS * 3600_000;
  const span = to - from;
  const marks = useMemo(() => {
    const out: number[] = [];
    for (let x = from; x < to; x += SLOT_MS) out.push(x);
    return out;
  }, [from, to]);
  const shift = (hours: number) => setStart(new Date(start.getTime() + hours * 3600_000));

  return (
    <div className="live-guide" data-live="guide">
      <div className="toolbar">
        <Button onClick={() => shift(-GUIDE_HOURS / 2)}>{t("live.guide.earlier")}</Button>
        <Button onClick={() => setStart(new Date(Math.floor(Date.now() / SLOT_MS) * SLOT_MS))}>
          {t("live.guide.nowButton")}
        </Button>
        <Button onClick={() => shift(GUIDE_HOURS / 2)}>{t("live.guide.later")}</Button>
        <span className="muted">{fmt.dateTime(new Date(from))}</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="live-guide-scroll">
        <div className="live-guide-row live-guide-head">
          <span className="live-guide-channel" />
          <span className="live-guide-track">
            {marks.map((m) => (
              <span key={m} className="live-guide-mark" style={{ left: `${((m - from) / span) * 100}%` }}>
                {fmt.time(new Date(m))}
              </span>
            ))}
            {Date.now() > from && Date.now() < to && (
              <span className="live-guide-nowline" style={{ left: `${((Date.now() - from) / span) * 100}%` }} />
            )}
          </span>
        </div>
        {channels.map((c) => {
          const slots = (c.xmltv_id && guide?.programmes[c.xmltv_id]) || [];
          return (
            <div key={c.id} className={c.id === current ? "live-guide-row current" : "live-guide-row"}>
              <Link to={`/live/${c.id}`} replace className="live-guide-channel">
                <Logo channel={c} />
                <span>{c.name}</span>
              </Link>
              <span className="live-guide-track">
                {slots.map((s) => {
                  const a = Math.max(from, Date.parse(s.start));
                  const b = Math.min(to, Date.parse(s.stop));
                  if (b <= a) return null;
                  return (
                    <button
                      type="button"
                      key={s.id}
                      className="live-guide-slot"
                      style={{ left: `${((a - from) / span) * 100}%`, width: `${((b - a) / span) * 100}%` }}
                      title={`${fmt.time(s.start)} ${slotText(s)}`}
                      onClick={() =>
                        void getProgramme(s.id)
                          .then((programme) => setDetail({ programme, channel: c }))
                          .catch((e) => setError(errorText(e)))
                      }
                    >
                      <span className="live-guide-time">{fmt.time(s.start)}</span> {s.title}
                    </button>
                  );
                })}
              </span>
            </div>
          );
        })}
      </div>
      {detail && (
        <aside className="live-detail" aria-labelledby="live-detail-title">
          <h3 id="live-detail-title">{slotText(detail.programme)}</h3>
          <p className="muted">
            {detail.channel.name} · {fmt.dateTime(detail.programme.start)}–{fmt.time(detail.programme.stop)}
            {detail.programme.category ? ` · ${detail.programme.category}` : ""}
          </p>
          {detail.programme.description && <p>{detail.programme.description}</p>}
          <div className="toolbar">
            <Button variant="primary" onClick={() => navigate(`/live/${detail.channel.id}`, { replace: true })}>
              {t("live.guide.watch")}
            </Button>
            <Button onClick={() => setDetail(null)}>{t("live.guide.close")}</Button>
          </div>
        </aside>
      )}
    </div>
  );
}

function Live({ params }: RouteProps): JSX.Element {
  const me = useCurrentUser();
  const [data, setData] = useState<Channels | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [onlyFav, setOnlyFav] = useState(false);
  const [tab, setTab] = useState<"channels" | "guide">("channels");
  const [now, setNow] = useState(() => Date.now());

  const reload = useCallback(async () => {
    setData(await getChannels());
  }, []);

  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
    // Nu en straks verversen: elke minuut de balkjes, elke 5 minuten de lijst
    const tick = setInterval(() => setNow(Date.now()), 60_000);
    const again = setInterval(() => void reload().catch(() => {}), 5 * 60_000);
    return () => {
      clearInterval(tick);
      clearInterval(again);
    };
  }, [reload]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.channels ?? []).filter(
      (c) =>
        (!q || c.name.toLowerCase().includes(q) || String(c.number ?? "") === q) &&
        (!group || c.group === group) &&
        (!onlyFav || c.favorite),
    );
  }, [data, query, group, onlyFav]);

  const current = data?.channels.find((c) => c.id === params.id) ?? null;

  const step = useCallback(
    (delta: number) => {
      const list = shown.length ? shown : (data?.channels ?? []);
      if (!list.length) return;
      const i = list.findIndex((c) => c.id === params.id);
      const next = list[(i + delta + list.length) % list.length];
      navigate(`/live/${next.id}`, { replace: true });
    },
    [shown, data, params.id],
  );

  useEffect(() => {
    // Kanaal+ en Kanaal- van de afstandsbediening (webOS: PageUp en PageDown)
    function onKey(e: KeyboardEvent) {
      if (e.key === "PageUp" || e.key === "ChannelUp") {
        e.preventDefault();
        step(1);
      } else if (e.key === "PageDown" || e.key === "ChannelDown") {
        e.preventDefault();
        step(-1);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  function toggleFavorite(c: Channel) {
    const on = !c.favorite;
    setData((d) => d && { ...d, channels: d.channels.map((x) => (x.key === c.key ? { ...x, favorite: on } : x)) });
    void setFavorite(c.key, on).catch((e) => {
      setError(errorText(e));
      void reload().catch(() => {});
    });
  }

  if (isGuest(me)) {
    return (
      <main className="wide live-page">
        <PageTitle>{t("live.title")}</PageTitle>
        <p>{t("live.emptyUser")}</p>
      </main>
    );
  }

  return (
    <main className="wide live-page" data-live="page">
      <PageTitle>{t("live.title")}</PageTitle>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data && data.channels.length === 0 ? (
        <EmptyState icon="video" title={t("live.empty")}>
          {me?.role === "admin" ? t("live.emptyAdmin") : t("live.emptyUser")}
        </EmptyState>
      ) : (
        <div className="live-layout">
          <Player channel={current} onStep={step} />
          <div className="live-side">
            <div className="toolbar live-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === "channels"}
                className={tab === "channels" ? "btn selected" : "btn"}
                onClick={() => setTab("channels")}
              >
                {t("live.tabs.channels")}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "guide"}
                className={tab === "guide" ? "btn selected" : "btn"}
                onClick={() => setTab("guide")}
              >
                {t("live.tabs.guide")}
              </button>
            </div>
            <div className="toolbar live-filters">
              <input
                type="search"
                name="live-q"
                aria-label={t("live.search")}
                placeholder={t("live.search")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {data && data.groups.length > 1 && (
                <select name="live-group" aria-label={t("live.group")} value={group} onChange={(e) => setGroup(e.target.value)}>
                  <option value="">{t("live.allGroups")}</option>
                  {data.groups.map((g) => (
                    <option key={g} value={g}>
                      {g}
                    </option>
                  ))}
                </select>
              )}
              <label>
                <input type="checkbox" checked={onlyFav} onChange={(e) => setOnlyFav(e.target.checked)} />{" "}
                {t("live.favorites")}
              </label>
            </div>
            {tab === "channels" ? (
              <ChannelList channels={shown} current={current?.id ?? null} now={now} onFavorite={toggleFavorite} />
            ) : (
              <GuideGrid channels={shown} current={current?.id ?? null} />
            )}
          </div>
        </div>
      )}
    </main>
  );
}

export default function LivePage(props: RouteProps): JSX.Element {
  return <MediaGate params={props.params}>{() => <Live {...props} />}</MediaGate>;
}
