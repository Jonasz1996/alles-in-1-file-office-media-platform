// De bediening van de speler (Keuze 32 en 33): spelen, de tijdbalk met buffer, hoofdstukken en
// sprites, tijd en volume, kwaliteit, audiospoor, ondertitels (ook inbranden), PiP, volledig
// scherm op de container (zodat onze knoppen er blijven), Info met de redenen, de kaart
// "Volgende aflevering", de Media Session en de toetsen. De sessie zelf zit in controller.ts.
//
// Op /kijken (where "watch") krijgt hij alle toetsen in een eigen scope "player"; in de viewer
// (where "viewer") alleen Spatie, K, J, L, M, C, A, P en 0–9 in de scope van de viewer.

import { type JSX, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { errorText, locale, t } from "../../i18n";
import { streamUrl } from "../../media";
import type { PlayOut, Quality } from "../../media/types";
import { Spinner } from "../../ui/Spinner";
import { useScope, useShortcuts } from "../../ui/keys";
import type { Size } from "../caps";
import { Controller } from "../controller";
import { sessionUrl } from "../engine";
import {
  bindings,
  chapterTarget,
  digitOf,
  type PlayerAction,
  percentTime,
  scopeFor,
  seekBy,
  volumeStep,
  type Where,
} from "../keys";
import { spans } from "../mse";
import { COUNTDOWN_S, cardTime, clock } from "../next";
import { loadProgress, Reporter, type ProgressSink } from "../playback";
import type { Notice, StopReason } from "../session";
import { waitVtt } from "../subs";
import {
  audioLabel,
  autoSubtitle,
  currentAudio,
  type LabelWords,
  nextAudio,
  srclang,
  subtitleLabel,
  textTracks,
  toggleSubtitle,
} from "../tracks";
import { parseTrickVtt, type TrickCue } from "../trick";
import { type PlayerIconName, PIcon } from "./icons";
import { type MenuChoice, PlayerMenu } from "./PlayerMenu";
import { Seekbar } from "./Seekbar";
import "./player.css";

export interface NextUp {
  nodeId: string;
  title: string;
  /** "S1 · A3" */
  tag?: string | null;
  /** Een volgend deel (cd2): zonder kaart, meteen verder */
  part: boolean;
}

export interface PlayerProps {
  nodeId: string;
  where: Where;
  title: string;
  /** Media Session: de serie */
  artist?: string | null;
  poster?: string | null;
  startS?: number;
  size?: Size;
  next?: NextUp | null;
  /** De vorige aflevering (Media Session) */
  prev?: string | null;
  onGo?(nodeId: string): void;
  /** Op /kijken: terug (de knop en Esc) */
  onBack?(): void;
  /** In de viewer: aan het begin lukt het niet; de <video> van fase 3 */
  onFallback?(): void;
}

type MenuKind = "quality" | "audio" | "subtitles";
type VttState = "ready" | "pending" | "failed";

const VOLUME_KEY = "player.volume.v1";
const AUTONEXT_KEY = "player.autonext.v1";
const IDLE_MS = 3000;

function readStore<T>(key: string, fallback: T, ok: (v: unknown) => v is T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    const v: unknown = JSON.parse(raw);
    return ok(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

function writeStore(key: string, v: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    // privé venster of vol: dan niet onthouden
  }
}

interface Volume {
  volume: number;
  muted: boolean;
}

const isVolume = (v: unknown): v is Volume =>
  typeof v === "object" &&
  v !== null &&
  typeof (v as Volume).volume === "number" &&
  (v as Volume).volume >= 0 &&
  (v as Volume).volume <= 1 &&
  typeof (v as Volume).muted === "boolean";

/** De naam van een kwaliteit: "Auto", "Origineel", "720p". */
export function qualityName(q: Quality): string {
  if (q === "auto") return t("player.quality.auto");
  if (q === "source") return t("player.quality.source");
  return t("player.quality.height", { height: q });
}

function words(): LabelWords {
  return {
    forced: t("player.subtitles.forced"),
    sdh: t("player.subtitles.sdh"),
    burn: (label) => t("player.subtitles.burn", { label }),
    unknown: t("player.subtitles.unknown"),
    channels: (n) =>
      n === 1
        ? t("player.audio.mono")
        : n === 2
          ? t("player.audio.stereo")
          : n === 6
            ? t("player.audio.surround")
            : n === 8
              ? t("player.audio.surround7")
              : t("player.audio.channels", { n }),
  };
}

type PipVideo = HTMLVideoElement & {
  webkitSetPresentationMode?: (mode: "inline" | "picture-in-picture" | "fullscreen") => void;
  webkitPresentationMode?: string;
  webkitEnterFullscreen?: () => void;
};

function pipAvailable(v: PipVideo | null): boolean {
  if (!v) return false;
  return (document.pictureInPictureEnabled && !v.disablePictureInPicture) || typeof v.webkitSetPresentationMode === "function";
}

export default function Player(props: PlayerProps): JSX.Element {
  const { where, nodeId } = props;
  const box = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const ctl = useRef<Controller | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const [, tick] = useReducer((n: number) => n + 1, 0);

  const [out, setOut] = useState<PlayOut | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "stopped" | "failed">("loading");
  const [notice, setNotice] = useState<Notice>(null);
  const [failure, setFailure] = useState<{ e: unknown; unreachable: boolean; loop: boolean } | null>(null);
  const [stopReason, setStopReason] = useState<StopReason | null>(null);
  const [paused, setPaused] = useState(true);
  const [waiting, setWaiting] = useState(false);
  const [time, setTime] = useState(props.startS ?? 0);
  const [vol, setVol] = useState<Volume>(() => readStore(VOLUME_KEY, { volume: 1, muted: false }, isVolume));
  const [sub, setSub] = useState<string | null>(null);
  const lastSub = useRef<string | null>(null);
  const subChosen = useRef(false);
  const [vtt, setVtt] = useState<Record<string, VttState>>({});
  const [menu, setMenu] = useState<MenuKind | null>(null);
  const [info, setInfo] = useState(false);
  const [idle, setIdle] = useState(false);
  const [full, setFull] = useState(false);
  const [say, setSay] = useState("");
  const [card, setCard] = useState<"off" | "shown" | "cancelled">("off");
  const [count, setCount] = useState(COUNTDOWN_S);
  const [autoNext, setAutoNext] = useState(() => readStore(AUTONEXT_KEY, true, (v): v is boolean => typeof v === "boolean"));
  const [cues, setCues] = useState<TrickCue[]>([]);
  const [scrubbing, setScrubbing] = useState(false);
  const want = useRef(true);
  const pendingSay = useRef<string | null>(null);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const menuButtons = {
    quality: useRef<HTMLButtonElement>(null),
    audio: useRef<HTMLButtonElement>(null),
    subtitles: useRef<HTMLButtonElement>(null),
  };

  const announce = useCallback((text: string) => {
    // Twee keer dezelfde tekst: eerst leeg, anders leest een schermlezer hem niet opnieuw
    setSay("");
    requestAnimationFrame(() => setSay(text));
  }, []);

  // --- de sessie ---
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const c = new Controller(
      v,
      nodeId,
      {
        session: (o, why) => {
          setOut(o);
          setPhase("ready");
          setFailure(null);
          setStopReason(null);
          if (why === "start") {
            setVtt({});
          }
          if (pendingSay.current) {
            announce(pendingSay.current);
            pendingSay.current = null;
          }
          if (want.current && v.paused) {
            v.play().catch((e: unknown) => {
              if ((e as { name?: string }).name === "NotAllowedError") want.current = false;
              setPaused(v.paused);
            });
          }
        },
        notice: setNotice,
        stopped: (r) => {
          setStopReason(r);
          setPhase("stopped");
          setNotice(null);
        },
        failed: (e, o) => {
          setNotice(null);
          if (o.atStart && latest.current.onFallback) {
            latest.current.onFallback();
            return;
          }
          setFailure({ e, unreachable: o.unreachable, loop: o.loop });
          setPhase("failed");
        },
        lowered: (q) => announce(t("player.quality.lowered", { quality: qualityName(q) })),
      },
      { size: latest.current.size, quick: where === "viewer" },
    );
    ctl.current = c;
    void c.open(latest.current.startS ?? 0);
    // Weg van de pagina (een ander adres, het tabblad dicht): de sessie meteen dicht; terug uit
    // de bfcache: een nieuwe sessie op dezelfde plek
    const hide = () => c.hide();
    const show = (e: PageTransitionEvent) => {
      if (e.persisted) void c.show();
    };
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    return () => {
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("pageshow", show);
      c.close();
      ctl.current = null;
    };
  }, [nodeId, where, announce]);

  // --- de voortgang (M7; zonder progress.ts doet dit niets) ---
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    let reporter: Reporter | null = null;
    let sink: ProgressSink | null = null;
    let live = true;
    void loadProgress().then((m) => {
      if (!live || !m) return;
      sink = m.attach({
        nodeId,
        position: () => ctl.current?.position() ?? v.currentTime,
        paused: () => v.paused,
        seek: (s) => {
          if (v.paused) v.currentTime = s;
        },
      });
      const s = sink;
      reporter = new Reporter(
        v,
        () => {
          const c = ctl.current;
          const o = c?.out;
          if (!c || !o) return null;
          return {
            position: c.position(),
            started_at: o.started_at,
            duration_ms: o.duration_ms,
            audio: c.choice.audio ?? null,
            subtitle: c.choice.burn ?? null,
          };
        },
        s,
      );
    });
    return () => {
      live = false;
      reporter?.stop();
      (sink as ProgressSink | null)?.detach();
    };
  }, [nodeId]);

  // --- de video ---
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const now = () => setTime(ctl.current?.position() ?? v.currentTime);
    const handlers: [string, () => void][] = [
      ["timeupdate", now],
      ["seeking", now],
      ["durationchange", tick],
      ["progress", tick],
      ["play", () => {
        want.current = true;
        setPaused(false);
      }],
      ["playing", () => setWaiting(false)],
      ["pause", () => {
        want.current = false;
        setPaused(true);
      }],
      ["waiting", () => setWaiting(true)],
      ["canplay", () => setWaiting(false)],
      ["volumechange", () => setVol({ volume: v.volume, muted: v.muted })],
      ["enterpictureinpicture", tick],
      ["leavepictureinpicture", tick],
    ];
    for (const [name, fn] of handlers) v.addEventListener(name, fn);
    return () => {
      for (const [name, fn] of handlers) v.removeEventListener(name, fn);
    };
  }, []);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    if (v.volume !== vol.volume) v.volume = vol.volume;
    if (v.muted !== vol.muted) v.muted = vol.muted;
    writeStore(VOLUME_KEY, vol);
  }, [vol]);

  useEffect(() => {
    const onFull = () => setFull(document.fullscreenElement === box.current && box.current !== null);
    document.addEventListener("fullscreenchange", onFull);
    return () => document.removeEventListener("fullscreenchange", onFull);
  }, []);

  // --- ondertitels ---
  const subs = out?.tracks.subtitles ?? [];
  const texts = textTracks(subs);
  const sid = out?.sid ?? null;
  const vttOf = (id: string): VttState => {
    const s = vtt[id];
    if (s) return s;
    const track = subs.find((x) => x.id === id);
    return track?.state === "pending" ? "pending" : track?.state === "failed" ? "failed" : "ready";
  };

  // De eerste keer (en na een ander audiospoor zolang de kijker niets koos): de regel voor geforceerd
  const audioNow = out ? currentAudio(out.tracks.audio, ctl.current?.choice.audio) : null;
  useEffect(() => {
    if (!out || subChosen.current) return;
    setSub(autoSubtitle(out.tracks.subtitles, audioNow?.lang));
  }, [out, audioNow?.lang]);

  // Een gekozen ondertitel die nog gemaakt wordt: wachten (202 met Retry-After)
  const selectedState = sub ? vttOf(sub) : null;
  useEffect(() => {
    if (!sid || !sub || selectedState !== "pending") return;
    let gone = false;
    const id = sub;
    void waitVtt(streamUrl(sid, `subs/${id}.vtt`), {
      fetchFn: (...a) => fetch(...a),
      sleep: (ms) => new Promise((ok) => setTimeout(ok, ms)),
      cancelled: () => gone,
    }).then((r) => {
      if (gone || r === "cancelled") return;
      setVtt((m) => ({ ...m, [id]: r }));
      if (r === "failed") {
        announce(t("player.subtitles.failed"));
        setSub((s) => (s === id ? null : s));
      }
    });
    return () => {
      gone = true;
    };
  }, [sid, sub, selectedState, announce]);

  // De modus van elke <track>: alleen de gekozen laat zich zien
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    for (const el of v.querySelectorAll<HTMLTrackElement>("track[data-id]")) {
      const mode = el.dataset.id === sub ? "showing" : "disabled";
      if (el.track.mode !== mode) el.track.mode = mode;
    }
  });

  const onTrackError = (id: string) => {
    // Een <track> die toch 202 kreeg (de ondertitel werd opnieuw gemaakt): wachten
    if (id === sub) setVtt((m) => ({ ...m, [id]: "pending" }));
  };

  // --- de sprites (trick.vtt) ---
  const trickUrl = out?.trick ? sessionUrl(out.sid, out.trick.url) : null;
  useEffect(() => {
    setCues([]);
    if (!trickUrl) return;
    const ctrl = new AbortController();
    const base = new URL(trickUrl, location.href).href;
    fetch(trickUrl, { signal: ctrl.signal, credentials: "omit" })
      .then((r) => (r.ok ? r.text() : ""))
      .then((text) => {
        if (!ctrl.signal.aborted && text) setCues(parseTrickVtt(text, base, location.origin));
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [trickUrl]);

  // --- de bediening verbergen na 3 s stilte (nooit met focus erin of een open menu) ---
  const wake = useCallback(() => {
    setIdle(false);
    if (idleTimer.current) clearTimeout(idleTimer.current);
    const later = () => {
      idleTimer.current = setTimeout(() => {
        const v = video.current;
        const b = box.current;
        if (!v || v.paused || !b) return;
        const a = document.activeElement;
        if (b.querySelector(".player-menu") || (a && b.contains(a) && a.matches(":focus-visible"))) {
          later();
          return;
        }
        setIdle(true);
      }, IDLE_MS);
    };
    later();
  }, []);

  useEffect(() => {
    wake();
    const onKey = () => wake();
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [wake, paused]);

  // --- de volgende aflevering ---
  const next = props.next ?? null;
  const v0 = video.current;
  const duration = out ? out.duration_ms / 1000 : v0 && Number.isFinite(v0.duration) ? v0.duration : 0;
  const cardAt = next && !next.part ? cardTime(duration, out?.credits_ms) : null;

  useEffect(() => {
    if (cardAt === null) return;
    if (time >= cardAt && card === "off") {
      setCard("shown");
      setCount(COUNTDOWN_S);
    } else if (time < cardAt - 1 && card !== "off") {
      // Teruggezocht: de kaart mag later weer komen
      setCard("off");
    }
  }, [time, cardAt, card]);

  const go = useCallback((id: string) => latest.current.onGo?.(id), []);

  useEffect(() => {
    if (card !== "shown" || !autoNext || paused || !next) return;
    if (count <= 0) {
      go(next.nodeId);
      return;
    }
    const timer = setTimeout(() => setCount((n) => n - 1), 1000);
    return () => clearTimeout(timer);
  }, [card, autoNext, paused, count, next, go]);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const onEnded = () => {
      const n = latest.current.next;
      if (!n) return;
      // Een film in delen: meteen het volgende deel; een aflevering alleen als de kaart niet weg is
      if (n.part || (autoNext && card !== "cancelled")) go(n.nodeId);
    };
    v.addEventListener("ended", onEnded);
    return () => v.removeEventListener("ended", onEnded);
  }, [autoNext, card, go]);

  // --- acties ---
  const toggle = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused || v.ended) {
      want.current = true;
      v.play().catch(() => setPaused(v.paused));
    } else v.pause();
  }, []);

  const seek = useCallback((to: number) => {
    const v = video.current;
    if (!v || !Number.isFinite(to)) return;
    v.currentTime = Math.max(0, to);
    setTime(Math.max(0, to));
  }, []);

  const position = () => ctl.current?.position() ?? video.current?.currentTime ?? 0;

  const toggleFull = useCallback(() => {
    const b = box.current;
    const v = video.current as PipVideo | null;
    if (!b) return;
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else if (b.requestFullscreen) void b.requestFullscreen().catch(() => v?.webkitEnterFullscreen?.());
    else v?.webkitEnterFullscreen?.();
  }, []);

  const togglePip = useCallback(() => {
    const v = video.current as PipVideo | null;
    if (!v) return;
    if (document.pictureInPictureElement === v) {
      void document.exitPictureInPicture().catch(() => {});
    } else if (typeof v.requestPictureInPicture === "function" && document.pictureInPictureEnabled) {
      void v.requestPictureInPicture().catch(() => {});
    } else if (typeof v.webkitSetPresentationMode === "function") {
      v.webkitSetPresentationMode(v.webkitPresentationMode === "picture-in-picture" ? "inline" : "picture-in-picture");
    }
  }, []);

  const chooseSub = (id: string | null) => {
    subChosen.current = true;
    if (id) lastSub.current = id;
    setSub(id);
    const c = ctl.current;
    if (c?.choice.burn) {
      pendingSay.current = id ? t("player.subtitles.on", { track: labelOf(id) }) : t("player.subtitles.none");
      void c.change({ burn: null });
    } else {
      announce(id ? t("player.subtitles.on", { track: labelOf(id) }) : t("player.subtitles.none"));
    }
  };

  const chooseBurn = (id: string) => {
    subChosen.current = true;
    setSub(null);
    pendingSay.current = t("player.subtitles.on", { track: labelOf(id) });
    void ctl.current?.change({ burn: id });
  };

  const chooseAudio = (index: number) => {
    const track = out?.tracks.audio.find((a) => a.index === index);
    pendingSay.current = track ? t("player.audio.changed", { track: audioLabel(track, locale(), words()) }) : null;
    void ctl.current?.change({ audio: index });
  };

  const chooseQuality = (q: Quality) => {
    pendingSay.current = t("player.quality.changed", { quality: qualityName(q) });
    void ctl.current?.change({ quality: q });
  };

  function labelOf(id: string): string {
    const s = subs.find((x) => x.id === id);
    if (s) return subtitleLabel(s, locale(), words());
    return t("player.subtitles.burnForced");
  }

  const cancelCard = () => setCard("cancelled");

  const escape = () => {
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => {});
      return;
    }
    if (card === "shown") {
      cancelCard();
      return;
    }
    if (info) {
      setInfo(false);
      return;
    }
    latest.current.onBack?.();
  };

  const chapters = out?.chapters ?? [];
  const audioTracks = out?.tracks.audio ?? [];

  const run = (action: PlayerAction, e: KeyboardEvent) => {
    const v = video.current;
    if (!v) return;
    wake();
    switch (action) {
      case "play":
        toggle();
        break;
      case "back10":
        seek(seekBy(position(), -10, duration));
        break;
      case "forward10":
        seek(seekBy(position(), 10, duration));
        break;
      case "back5":
        seek(seekBy(position(), -5, duration));
        break;
      case "forward5":
        seek(seekBy(position(), 5, duration));
        break;
      case "volumeUp":
        setVol((s) => ({ volume: volumeStep(s.volume, 1), muted: false }));
        break;
      case "volumeDown":
        setVol((s) => ({ ...s, volume: volumeStep(s.volume, -1) }));
        break;
      case "mute":
        setVol((s) => ({ ...s, muted: !s.muted }));
        break;
      case "fullscreen":
        toggleFull();
        break;
      case "subtitles": {
        const to = toggleSubtitle(sub ?? (ctl.current?.choice.burn || null), lastSub.current, subs);
        const s = to ? subs.find((x) => x.id === to) : null;
        if (s?.state === "burn") chooseBurn(s.id);
        else chooseSub(to);
        break;
      }
      case "audio": {
        const to = nextAudio(audioTracks, ctl.current?.choice.audio);
        if (to !== null) chooseAudio(to);
        break;
      }
      case "pip":
        togglePip();
        break;
      case "prevChapter":
      case "nextChapter": {
        const to = chapterTarget(
          chapters.map((c) => c.start_ms / 1000),
          position(),
          action === "nextChapter" ? 1 : -1,
        );
        if (to !== null) seek(to);
        break;
      }
      case "percent": {
        const d = digitOf(e);
        if (d !== null) seek(percentTime(d, duration));
        break;
      }
      case "begin":
        seek(0);
        break;
      case "end":
        seek(Math.max(0, duration - 0.5));
        break;
      case "next":
        if (next) go(next.nodeId);
        break;
      case "info":
        setInfo((x) => !x);
        break;
      case "escape":
        escape();
        break;
    }
  };

  const can = (action: PlayerAction): boolean => {
    switch (action) {
      case "next":
        return !!next;
      case "audio":
        return audioTracks.length > 1;
      case "subtitles":
        return subs.length > 0;
      case "pip":
        return pipAvailable(video.current);
      case "prevChapter":
      case "nextChapter":
        return chapters.length > 0;
      default:
        return true;
    }
  };

  useScope("player", where === "watch");
  useShortcuts(
    bindings(where, { run, can, label: (a) => t(`player.keys.${a}`) }),
    { scope: scopeFor(where), enabled: phase !== "stopped" },
  );

  // --- de Media Session (titel, serie, hoes; spelen, pauze, zoeken, vorige en volgende) ---
  const { title, artist, poster, prev } = props;
  useEffect(() => {
    const ms = navigator.mediaSession;
    if (!ms || typeof MediaMetadata === "undefined") return;
    try {
      ms.metadata = new MediaMetadata({
        title,
        artist: artist ?? "",
        artwork: poster ? [{ src: new URL(poster, location.href).href }] : [],
      });
    } catch {
      // een oude browser
    }
    const set = (a: MediaSessionAction, fn: MediaSessionActionHandler | null) => {
      try {
        ms.setActionHandler(a, fn);
      } catch {
        // deze actie kent de browser niet
      }
    };
    const v = video.current;
    set("play", () => {
      want.current = true;
      void v?.play().catch(() => {});
    });
    set("pause", () => v?.pause());
    set("seekbackward", (d) => seek(seekBy(position(), -(d.seekOffset ?? 10), duration)));
    set("seekforward", (d) => seek(seekBy(position(), d.seekOffset ?? 10, duration)));
    set("seekto", (d) => {
      if (typeof d.seekTime === "number") seek(d.seekTime);
    });
    set("previoustrack", prev ? () => go(prev) : null);
    set("nexttrack", next ? () => go(next.nodeId) : null);
    return () => {
      for (const a of ["play", "pause", "seekbackward", "seekforward", "seekto", "previoustrack", "nexttrack"] as const) {
        set(a, null);
      }
      ms.metadata = null;
    };
    // position() en duration lezen de handlers op het moment zelf
  }, [title, artist, poster, prev, next, go, seek]);

  // --- tekenen ---
  const v = video.current;
  const buffered = v && duration > 0 ? spans(v.buffered) : [];
  const loc = locale();
  const w = useMemo(words, [loc]);
  const choice = ctl.current?.choice ?? {};
  const burnNow = choice.burn ?? null;
  const qualityNow: Quality = choice.quality ?? "auto";
  const showControls = !idle || paused || menu !== null || phase !== "ready";
  const busy = phase === "loading" || (waiting && !paused) || (ctl.current?.busy ?? false);

  const subChoices: MenuChoice[] = [
    { key: "off", label: t("player.subtitles.off"), checked: !sub && !burnNow, select: () => chooseSub(null) },
    ...subs
      .filter((s) => s.state !== "failed")
      .map<MenuChoice>((s) =>
        s.state === "burn"
          ? { key: s.id, label: subtitleLabel(s, loc, w), checked: burnNow === s.id, select: () => chooseBurn(s.id) }
          : { key: s.id, label: subtitleLabel(s, loc, w), checked: sub === s.id && !burnNow, select: () => chooseSub(s.id) },
      ),
  ];
  const forced = out?.forced_available;
  if (forced && !subs.some((s) => s.id === forced)) {
    subChoices.push({
      key: forced,
      label: t("player.subtitles.burnForced"),
      checked: burnNow === forced,
      select: () => chooseBurn(forced),
    });
  }
  const audioChoices: MenuChoice[] = audioTracks.map((a) => ({
    key: String(a.index),
    label: audioLabel(a, loc, w),
    checked: audioNow?.index === a.index,
    select: () => chooseAudio(a.index),
  }));
  const qualityChoices: MenuChoice[] = (out?.quality.options ?? []).map((q) => ({
    key: String(q),
    label:
      q === "auto" && qualityNow === "auto" && v?.videoHeight
        ? `${qualityName("auto")} (${t("player.quality.height", { height: v.videoHeight })})`
        : qualityName(q),
    checked: q === qualityNow,
    select: () => chooseQuality(q),
  }));

  const button = (icon: PlayerIconName, label: string, onClick: () => void, extra: Record<string, unknown> = {}) => (
    <button type="button" className="player-button" aria-label={label} title={label} onClick={onClick} {...extra}>
      <PIcon name={icon} />
    </button>
  );

  const menuButton = (kind: MenuKind, icon: PlayerIconName, label: string) => (
    <button
      ref={menuButtons[kind]}
      type="button"
      className="player-button"
      aria-label={label}
      title={label}
      aria-haspopup="menu"
      aria-expanded={menu === kind}
      aria-controls={menu === kind ? `player-menu-${kind}` : undefined}
      onClick={() => setMenu((m) => (m === kind ? null : kind))}
    >
      <PIcon name={icon} />
    </button>
  );

  let overlay: JSX.Element | null = null;
  if (phase === "stopped" && stopReason) {
    overlay = (
      <div className="player-overlay" role="alert">
        <p>{t(`player.stopped.${stopReason}`)}</p>
      </div>
    );
  } else if (phase === "failed" && failure) {
    const err = failure.e as { code?: string; params?: { can_raw?: boolean } };
    const text = failure.unreachable
      ? t("player.errors.unreachable")
      : failure.loop
        ? t("player.errors.loop")
        : err?.code === "media_decode"
          ? t("player.errors.decode")
          : errorText(failure.e);
    overlay = (
      <div className="player-overlay" role="alert">
        <p>{text}</p>
        <div className="player-overlay-actions">
          {err?.code === "media_hdr" && err.params?.can_raw && (
            <button type="button" className="btn" onClick={() => void ctl.current?.again({ hdr: "raw" })}>
              {t("player.errors.hdrRaw")}
            </button>
          )}
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              setPhase("loading");
              setFailure(null);
              void ctl.current?.again();
            }}
          >
            {t("player.errors.retry")}
          </button>
        </div>
      </div>
    );
  }

  const noticeText =
    notice?.kind === "reconnecting"
      ? t("player.notices.reconnecting")
      : notice?.kind === "busy"
        ? t("player.notices.busy", { message: errorText(notice.error) })
        : selectedState === "pending"
          ? t("player.subtitles.pending")
          : null;

  const reasons = out?.reasons ?? [];

  return (
    <div
      ref={box}
      className={`player player-${where}${showControls ? "" : " player-idle"}${full ? " player-full" : ""}`}
      data-state={phase}
      data-mode={out?.mode}
      role="region"
      aria-label={t("player.label")}
      onPointerMove={wake}
      onPointerDown={wake}
    >
      <video
        ref={video}
        className="player-video"
        playsInline
        preload="auto"
        poster={props.poster ?? undefined}
        onClick={toggle}
        onDoubleClick={toggleFull}
      >
        {sid &&
          texts
            .filter((s) => vttOf(s.id) === "ready")
            .map((s) => (
              <track
                key={`${sid}:${s.id}`}
                data-id={s.id}
                kind="subtitles"
                src={streamUrl(sid, `subs/${s.id}.vtt`)}
                srcLang={srclang(s.lang) || undefined}
                label={subtitleLabel(s, loc, w)}
                onError={() => onTrackError(s.id)}
              />
            ))}
      </video>

      {where === "watch" && (
        <div className="player-top">
          {button("chevron-left", t("player.back"), () => latest.current.onBack?.())}
          <h1 className="player-title" tabIndex={-1} data-page-title="">
            {props.title}
          </h1>
        </div>
      )}

      {busy && !overlay && (
        <div className="player-busy">
          <Spinner label={t("player.loading")} />
        </div>
      )}

      {paused && phase === "ready" && !busy && !overlay && (
        <button type="button" className="player-big-play" aria-label={t("player.play")} onClick={toggle}>
          <PIcon name="play" />
        </button>
      )}

      {overlay}

      {noticeText && !overlay && (
        <p className="player-notice" role="status">
          {noticeText}
        </p>
      )}

      {card === "shown" && next && (
        <section className="player-next" aria-label={next.part ? t("player.next.part") : t("player.next.title")}>
          <p className="player-next-head">{t("player.next.title")}</p>
          <p className="player-next-name">
            {next.tag && <span className="player-next-tag">{next.tag} · </span>}
            {next.title}
          </p>
          {autoNext && !paused && <p className="player-next-count">{t("player.next.in", { n: count })}</p>}
          <div className="player-next-actions">
            <button type="button" className="btn primary" onClick={() => go(next.nodeId)}>
              {t("player.next.now")}
            </button>
            <button type="button" className="btn" onClick={cancelCard}>
              {t("player.next.cancel")}
            </button>
          </div>
          <label className="player-next-auto">
            <input
              type="checkbox"
              checked={autoNext}
              onChange={(e) => {
                setAutoNext(e.target.checked);
                writeStore(AUTONEXT_KEY, e.target.checked);
              }}
            />
            {t("player.next.auto")}
          </label>
        </section>
      )}

      {info && out && (
        <section className="player-info" aria-label={t("player.info.title")}>
          <div className="player-info-head">
            <h2>{t("player.info.title")}</h2>
            {button("close", t("player.info.close"), () => setInfo(false))}
          </div>
          <dl>
            <dt>{t("player.info.mode")}</dt>
            <dd>{t(`player.info.modes.${out.mode}`)}</dd>
            <dt>{t("player.info.reasons")}</dt>
            <dd>
              {reasons.length ? (
                <ul>
                  {reasons.map((r) => (
                    <li key={r}>{t(`player.reasons.${r}`)}</li>
                  ))}
                </ul>
              ) : (
                t("player.info.direct")
              )}
            </dd>
            <dt>{t("player.info.format")}</dt>
            <dd className="player-mono">{out.mime}</dd>
            {v && v.videoHeight > 0 && (
              <>
                <dt>{t("player.info.picture")}</dt>
                <dd>{`${v.videoWidth}×${v.videoHeight}`}</dd>
              </>
            )}
            <dt>{t("player.info.buffer")}</dt>
            <dd>{t("player.info.seconds", { n: Math.round(ctl.current?.ahead() ?? 0) })}</dd>
          </dl>
        </section>
      )}

      <div className={`player-controls${showControls || scrubbing ? "" : " hidden"}`}>
        <Seekbar
          time={time}
          duration={duration}
          buffered={buffered}
          chapters={chapters}
          cues={cues}
          onSeek={seek}
          onScrub={setScrubbing}
        />
        <div className="player-bar">
          {button(paused ? "play" : "pause", paused ? t("player.play") : t("player.pause"), toggle)}
          {button("restore", t("player.back10"), () => seek(seekBy(position(), -10, duration)))}
          {button("rotate", t("player.forward10"), () => seek(seekBy(position(), 10, duration)))}
          {button(vol.muted || vol.volume === 0 ? "muted" : "volume", vol.muted ? t("player.unmute") : t("player.mute"), () =>
            setVol((s) => ({ ...s, muted: !s.muted })),
          )}
          <input
            type="range"
            className="player-volume"
            min={0}
            max={1}
            step={0.05}
            value={vol.muted ? 0 : vol.volume}
            aria-label={t("player.volume")}
            aria-valuetext={`${Math.round((vol.muted ? 0 : vol.volume) * 100)} %`}
            onChange={(e) => setVol({ volume: Number(e.target.value), muted: false })}
          />
          <span className="player-time">{t("player.time", { time: clock(time), duration: clock(duration) })}</span>
          <span className="player-spacer" />
          {subs.length > 0 && menuButton("subtitles", "cc", t("player.subtitles.label"))}
          {audioTracks.length > 1 && menuButton("audio", "speech", t("player.audio.label"))}
          {(out?.quality.options.length ?? 0) > 1 && menuButton("quality", "settings", t("player.quality.label"))}
          {button("info", t("player.info.title"), () => setInfo((x) => !x), { "aria-pressed": info })}
          {pipAvailable(v as PipVideo | null) && button("pip", t("player.pip"), togglePip)}
          {button(full ? "unfull" : "fullscreen", full ? t("player.exitFullscreen") : t("player.fullscreen"), toggleFull)}
        </div>
        {menu && (
          <PlayerMenu
            id={`player-menu-${menu}`}
            label={t(`player.${menu}.label`)}
            choices={menu === "subtitles" ? subChoices : menu === "audio" ? audioChoices : qualityChoices}
            anchor={menuButtons[menu]}
            onClose={() => setMenu(null)}
          />
        )}
      </div>

      <div className="sr-only" role="status" aria-live="polite">
        {say}
      </div>
    </div>
  );
}

