// De tijdbalk (Keuze 32 en 33): role="slider" met aria-valuetext "12:34 van 1:45:00", de buffer,
// streepjes bij de hoofdstukken, en bij hover en slepen de tijd met het plaatje uit trick.vtt
// (het vak xywh uit de tegel) en de titel van het hoofdstuk. Met de focus op de balk zoeken ← en
// → 5 s en Home en End naar begin en eind (ook in de viewer, waar die toetsen anders van de
// viewer zijn).

import { type JSX, type KeyboardEvent, type PointerEvent, useRef, useState } from "react";
import { t } from "../../i18n";
import type { Chapter } from "../../media/types";
import { seekBy } from "../keys";
import type { Span } from "../mse";
import { clock } from "../next";
import { type TrickCue, trickAt } from "../trick";

const STEP_S = 5;

/** De tijd (s) onder x in een balk van left tot left + width. */
export function timeAt(x: number, left: number, width: number, duration: number): number {
  if (!(width > 0) || !(duration > 0)) return 0;
  return Math.max(0, Math.min(1, (x - left) / width)) * duration;
}

/** Het hoofdstuk bij tijd t (s), of null. */
export function chapterAt(chapters: readonly Chapter[], t: number): Chapter | null {
  const ms = t * 1000;
  return chapters.find((c) => ms >= c.start_ms && ms < c.end_ms) ?? null;
}

export function Seekbar({
  time,
  duration,
  buffered,
  chapters,
  cues,
  onSeek,
  onScrub,
}: {
  time: number;
  duration: number;
  buffered: readonly Span[];
  chapters: readonly Chapter[];
  cues: readonly TrickCue[];
  onSeek(t: number): void;
  /** Slepen begint (true) of eindigt (false) */
  onScrub?(active: boolean): void;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ t: number; x: number } | null>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const shown = drag ?? time;
  const pct = (s: number) => (duration > 0 ? `${Math.max(0, Math.min(100, (s / duration) * 100))}%` : "0%");

  const at = (e: PointerEvent<HTMLDivElement>) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return null;
    return { t: timeAt(e.clientX, r.left, r.width, duration), x: Math.max(0, Math.min(r.width, e.clientX - r.left)) };
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    let to: number | null = null;
    if (e.key === "ArrowLeft") to = seekBy(time, -STEP_S, duration);
    else if (e.key === "ArrowRight") to = seekBy(time, STEP_S, duration);
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = duration;
    if (to === null) return;
    e.preventDefault();
    onSeek(to);
  };

  const preview = drag !== null && hover ? { ...hover, t: drag } : hover;
  const cue = preview ? trickAt(cues, preview.t) : null;
  const chapter = preview ? chapterAt(chapters, preview.t) : null;
  const current = chapterAt(chapters, time);
  const valueText = t("player.seek.valueText", { time: clock(shown), duration: clock(duration) });

  return (
    <div className="player-seek">
      <div
        ref={ref}
        className="player-seek-track"
        role="slider"
        tabIndex={0}
        aria-label={t("player.seek.label")}
        aria-valuemin={0}
        aria-valuemax={Math.max(0, Math.round(duration))}
        aria-valuenow={Math.round(shown)}
        aria-valuetext={current?.title ? `${valueText}, ${t("player.seek.chapter", { title: current.title })}` : valueText}
        onKeyDown={onKeyDown}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          const p = at(e);
          if (!p) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          setHover(p);
          setDrag(p.t);
          onScrub?.(true);
        }}
        onPointerMove={(e) => {
          const p = at(e);
          if (!p) return;
          setHover(p);
          if (drag !== null) setDrag(p.t);
        }}
        onPointerUp={(e) => {
          if (drag === null) return;
          const p = at(e);
          setDrag(null);
          onScrub?.(false);
          onSeek(p ? p.t : drag);
        }}
        onPointerCancel={() => {
          setDrag(null);
          onScrub?.(false);
        }}
        onPointerLeave={() => {
          if (drag === null) setHover(null);
        }}
      >
        <div className="player-seek-rail" aria-hidden>
          {buffered.map(([a, b]) => (
            <span key={`${a}`} className="player-seek-buffer" style={{ left: pct(a), width: pct(b - a) }} />
          ))}
          <span className="player-seek-played" style={{ width: pct(shown) }} />
          {chapters
            .filter((c) => c.start_ms > 0)
            .map((c) => (
              <span key={c.start_ms} className="player-seek-mark" style={{ left: pct(c.start_ms / 1000) }} />
            ))}
          <span className="player-seek-thumb" style={{ left: pct(shown) }} />
        </div>
      </div>
      {preview && duration > 0 && (
        <div className="player-seek-preview" style={{ left: `${preview.x}px` }} aria-hidden>
          {cue && (
            <span
              className="player-seek-sprite"
              data-start={cue.start}
              style={{
                width: `${cue.w}px`,
                height: `${cue.h}px`,
                backgroundImage: `url("${cue.url}")`,
                backgroundPosition: `-${cue.x}px -${cue.y}px`,
              }}
            />
          )}
          {chapter?.title && <span className="player-seek-chapter">{chapter.title}</span>}
          <span className="player-seek-time">{clock(preview.t)}</span>
        </div>
      )}
    </div>
  );
}
