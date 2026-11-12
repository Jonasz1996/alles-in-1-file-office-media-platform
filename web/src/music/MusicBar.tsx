// De muziekbalk (fase 5, M9; Keuze 37): onderaan de schil, met hoes, titel, artiest, vorige,
// spelen, volgende, de tijdlijn, het volume en de rij. App.tsx laadt dit chunk (en het gebied
// "music") pas als musicBar.visible() waar is, en houdt het buiten de pagina: hij blijft staan
// bij navigeren. Het <audio>-element zelf hangt aan <body> (player.ts), dus de muziek speelt
// door, ook als React de balk opnieuw tekent.
//
// Toetsenbord: alles zijn knoppen en schuiven. De tijdlijn: pijltjes per seconde, PageUp en
// PageDown per tien procent, Home en End. Escape sluit de rij en zet de focus terug op de knop.

import { type JSX, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { fmt, t } from "../i18n";
import { Link } from "../links";
import { albumPath } from "../media";
import { Button, IconButton } from "../ui/Button";
import { Cover, useMusicOwner } from "./parts";
import { player } from "./player";
import { queue } from "./queue";
import { artistName } from "./tracks";
import "./music.css";

/** Hooguit zoveel nummers in de lijst van de rij (vanaf het huidige). */
export const QUEUE_SHOWN = 100;

function time(s: number | null): string {
  return s === null || !Number.isFinite(s) ? "0:00" : fmt.duration(Math.max(0, Math.floor(s)));
}

function QueuePanel({ id, onClose }: { id: string; onClose(): void }): JSX.Element {
  const q = useSyncExternalStore(queue.subscribe, queue.snapshot, queue.snapshot);
  const from = Math.max(0, q.index);
  const shown = q.items.slice(from, from + QUEUE_SHOWN);
  const rest = q.items.length - from - shown.length;
  return (
    <div
      id={id}
      className="music-queue"
      role="region"
      aria-label={t("music.queue.label")}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="music-queue-head">
        <h2>{t("music.queue.label")}</h2>
        <Button variant="ghost" onClick={() => player.stop()} disabled={!q.items.length}>
          {t("music.queue.clear")}
        </Button>
      </div>
      {!shown.length && <p className="muted">{t("music.queue.empty")}</p>}
      <ol className="music-queue-list">
        {shown.map((item, k) => {
          const i = from + k;
          return (
            <li key={item.uid} className={i === q.index ? "now" : undefined}>
              <button
                type="button"
                className="music-queue-play"
                aria-current={i === q.index ? "true" : undefined}
                aria-label={t("music.queue.play", { title: item.title })}
                onClick={() => void player.playAt(i)}
              >
                <span className="music-queue-title">{item.title}</span>
                <span className="muted">
                  {i === q.index ? t("music.queue.now") : artistName(item.artist)}
                </span>
              </button>
              <IconButton
                icon="close"
                label={t("music.queue.remove", { title: item.title })}
                onClick={() => player.removeFromQueue(i)}
              />
            </li>
          );
        })}
      </ol>
      {rest > 0 && <p className="muted">{t("music.queue.rest", { n: rest })}</p>}
    </div>
  );
}

export default function MusicBar(): JSX.Element {
  useMusicOwner();
  const state = useSyncExternalStore(player.subscribe, player.snapshot, player.snapshot);
  const q = useSyncExternalStore(queue.subscribe, queue.snapshot, queue.snapshot);
  const item = q.items[q.index] ?? null;
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [draft, setDraft] = useState<number | null>(null);
  const dragging = useRef(false);

  // Een lege rij (het laatste nummer eruit): de rij dicht
  useEffect(() => {
    if (!q.items.length) setOpen(false);
  }, [q.items.length]);

  const playing = state.status === "playing" || state.status === "loading";
  const duration = state.duration ?? (item?.duration_ms ? item.duration_ms / 1000 : null);
  const position = draft ?? state.position;
  const max = duration !== null && Number.isFinite(duration) && duration > 0 ? Math.floor(duration) : 0;
  const closeQueue = () => {
    setOpen(false);
    toggleRef.current?.focus();
  };

  return (
    <section className="music-bar" aria-label={t("music.bar.label")}>
      <div className="music-bar-now">
        <Cover src={item?.cover} size={24} />
        <div className="music-bar-text">
          <span className="music-bar-title">
            {item?.album_id ? <Link to={albumPath(item.album_id)}>{item.title}</Link> : (item?.title ?? "")}
          </span>
          <span className="music-bar-artist muted">{item ? artistName(item.artist) : ""}</span>
        </div>
      </div>
      <div className="music-bar-controls">
        <div className="music-bar-buttons">
          <IconButton
            icon="chevron-left"
            label={t("music.bar.previous")}
            onClick={() => player.previous()}
            disabled={!item}
            data-music="previous"
          />
          <IconButton
            icon={playing ? "pause" : "play"}
            label={playing ? t("music.bar.pause") : t("music.bar.play")}
            className="music-bar-play"
            onClick={() => player.toggle()}
            disabled={!item}
            data-music="toggle"
          />
          <IconButton
            icon="chevron-right"
            label={t("music.bar.next")}
            onClick={() => player.next()}
            disabled={q.index + 1 >= q.items.length}
            data-music="next"
          />
        </div>
        <div className="music-bar-seek">
          <span className="music-bar-time muted" aria-hidden="true">
            {time(position)}
          </span>
          <input
            type="range"
            min={0}
            max={max}
            step={1}
            value={Math.min(max, Math.floor(position))}
            disabled={!item || max === 0}
            aria-label={t("music.bar.seek")}
            aria-valuetext={t("music.bar.time", { position: time(position), duration: time(duration) })}
            onPointerDown={() => {
              dragging.current = true;
            }}
            onPointerUp={(e) => {
              dragging.current = false;
              const v = Number((e.target as HTMLInputElement).value);
              setDraft(null);
              player.seek(v);
            }}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (dragging.current) setDraft(v);
              else player.seek(v);
            }}
            data-music="seek"
          />
          <span className="music-bar-time muted" aria-hidden="true">
            {time(duration)}
          </span>
        </div>
        {state.status === "loading" && (
          <span className="sr-only" role="status">
            {t("music.bar.loading")}
          </span>
        )}
        {!state.gapless && state.status !== "idle" && <p className="music-bar-note muted">{t("music.bar.gaps")}</p>}
        {state.status === "error" && (
          <div className="music-bar-error" role="alert">
            <span>{state.error}</span>
            <Button variant="link" onClick={() => player.toggle()}>
              {t("music.bar.retry")}
            </Button>
          </div>
        )}
      </div>
      <div className="music-bar-side">
        <IconButton
          icon="audio"
          label={state.muted ? t("music.bar.unmute") : t("music.bar.mute")}
          pressed={state.muted}
          onClick={() => player.toggleMute()}
        />
        <input
          type="range"
          className="music-bar-volume"
          min={0}
          max={100}
          step={5}
          value={Math.round((state.muted ? 0 : state.volume) * 100)}
          aria-label={t("music.bar.volume")}
          onChange={(e) => player.setVolume(Number(e.target.value) / 100)}
        />
        <IconButton
          ref={toggleRef}
          icon="list"
          label={t("music.bar.queue")}
          pressed={open}
          aria-controls={open ? panelId : undefined}
          onClick={() => setOpen((v) => !v)}
          data-music="queue"
        />
        <IconButton icon="close" label={t("music.bar.close")} onClick={() => player.stop()} data-music="close" />
      </div>
      {open && <QueuePanel id={panelId} onClose={closeQueue} />}
    </section>
  );
}
