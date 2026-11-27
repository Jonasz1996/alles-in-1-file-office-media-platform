// De scrubber naast de tijdlijn: een balk over de volle hoogte met de jaren erop. Slepen (of
// klikken) springt naar die plek; met het toetsenbord is het een schuif (role=slider) over de
// maanden: ↑/↓ een maand, PageUp/PageDown een jaar, Home/End de nieuwste/oudste maand.

import {
  type JSX,
  type KeyboardEvent,
  type PointerEvent,
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { t } from "../i18n";
import { monthLabel } from "./api";
import {
  fractionToOffset,
  type Layout,
  offsetToFraction,
  scrubStep,
  sectionAtOffset,
  sectionOffset,
  yearMarks,
} from "./timeline";

export interface ScrubberProps {
  layout: Layout;
  months: readonly string[];
  /** De bovenkant van de tijdlijn in het document (px) */
  listTop(): number;
  /** Naar een plek in de tijdlijn gesprongen (px vanaf de bovenkant van de tijdlijn) */
  onJump?(y: number): void;
}

/** Tekent alleen opnieuw als de indeling, de maanden of de maand bovenaan verandert. */
export const Scrubber = memo(function Scrubber({ layout, months, listTop, onJump }: ScrubberProps): JSX.Element | null {
  const track = useRef<HTMLDivElement>(null);
  const thumb = useRef<HTMLSpanElement>(null);
  // De maand bovenaan (voor aria-valuenow en het label) is state; de plek van het streepje
  // zetten we rechtstreeks (CSSOM), zodat scrollen niet elk frame React laat tekenen
  const [section, setSection] = useState(0);
  const fraction = useRef(0);
  const [dragging, setDragging] = useState(false);
  const frame = useRef(0);
  /** De maand waar het toetsenbord heen sprong: die blijft de waarde zolang hij in beeld staat
   * (onderaan kan de pagina niet verder scrollen, dan staat er een eerdere maand bovenaan) */
  const pinned = useRef(-1);

  const read = useCallback(() => {
    const y = window.scrollY - listTop();
    const viewport = window.innerHeight;
    fraction.current = offsetToFraction(layout, y, viewport);
    if (thumb.current) thumb.current.style.top = `${fraction.current * 100}%`;
    let section = Math.max(0, sectionAtOffset(layout, Math.max(0, y)));
    const p = pinned.current;
    if (p >= 0 && p < layout.counts.length) {
      const top = sectionOffset(layout, p);
      if (top >= y - 1 && top < y + viewport) section = p;
      else pinned.current = -1;
    }
    setSection(section);
  }, [layout, listTop]);

  useEffect(() => {
    read();
    const onScroll = () => {
      if (frame.current) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = 0;
        read();
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [read]);

  const jumpTo = useCallback(
    (y: number) => {
      window.scrollTo({ top: Math.max(0, listTop() + y) });
      onJump?.(y);
    },
    [listTop, onJump],
  );

  const toFraction = (clientY: number) => {
    const box = track.current?.getBoundingClientRect();
    if (!box || box.height <= 0) return 0;
    return (clientY - box.top) / box.height;
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    e.currentTarget.focus({ preventScroll: true });
    pinned.current = -1;
    setDragging(true);
    jumpTo(fractionToOffset(layout, toFraction(e.clientY), window.innerHeight));
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    jumpTo(fractionToOffset(layout, toFraction(e.clientY), window.innerHeight));
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setDragging(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const step =
      e.key === "ArrowUp"
        ? "prev"
        : e.key === "ArrowDown"
          ? "next"
          : e.key === "PageUp"
            ? "prevYear"
            : e.key === "PageDown"
              ? "nextYear"
              : e.key === "Home"
                ? "first"
                : e.key === "End"
                  ? "last"
                  : null;
    if (!step) return;
    e.preventDefault();
    const s = scrubStep(months, section, step);
    if (s < 0) return;
    pinned.current = s;
    setSection(s);
    jumpTo(sectionOffset(layout, s));
  };

  if (months.length === 0) return null;
  const marks = yearMarks(layout, months, window.innerHeight);
  const current = months[Math.min(section, months.length - 1)];
  return (
    <div
      ref={track}
      className="scrubber"
      role="slider"
      tabIndex={0}
      aria-label={t("photos.scrubber")}
      aria-orientation="vertical"
      aria-valuemin={1}
      aria-valuemax={months.length}
      aria-valuenow={section + 1}
      aria-valuetext={monthLabel(current)}
      data-dragging={dragging || undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
    >
      {marks.map((m) => (
        <span key={m.year} className="scrubber-year" aria-hidden="true" style={{ top: `${m.fraction * 100}%` }}>
          {m.year}
        </span>
      ))}
      <span ref={thumb} className="scrubber-thumb" aria-hidden="true" style={{ top: `${fraction.current * 100}%` }}>
        <span className="scrubber-label">{monthLabel(current)}</span>
      </span>
    </div>
  );
});
