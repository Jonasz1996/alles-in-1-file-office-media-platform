// Welke rijen van het fotoraster er staan, bij scrollen op het venster. Als useVirtualRows
// (ui/virtual.tsx), met dezelfde pure functies, maar met één verschil: een sprong (Home, End,
// de scrubber, terug naar de foto met de focus) waarbij geen enkele rij blijft staan, tekent
// React als transition. Dan rendert hij in stukjes van een paar ms in plaats van één lange taak
// voor een heel scherm nieuwe cellen; de rij met de focus staat er meteen (keepIndex). Zo'n
// sprong komt in twee frames: eerst wat in beeld is, dan de marge (firstStep, nextStep).

import { startTransition, useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { extraIndex, type Range, scrollTarget, variableRange } from "../ui/virtual";

export interface WindowRows {
  start: number;
  end: number;
  totalHeight: number;
  offsetOf(i: number): number;
  scrollToIndex(i: number, align?: "auto" | "start" | "center"): void;
  extra: number | null;
}

/** Overlappen twee bereiken (dan is het gewoon scrollen, geen sprong)? */
export function overlaps(a: Range, b: Range): boolean {
  return a.start < b.end && b.start < a.end;
}

/**
 * Wat er na een nieuw gewenst bereik (next) als eerste getekend wordt. Gewoon scrollen (het
 * overlapt wat er al was): meteen alles. Een sprong: eerst alleen wat in beeld komt (view, zonder
 * marge); de marge erboven en eronder volgt in een volgend frame (nextStep). Een heel scherm
 * nieuwe cellen plus de marge in één frame was bij 4× CPU een frame van 60 ms of meer (layout).
 */
export function firstStep(shown: Range, next: Range, view: Range): Range {
  return overlaps(shown, next) ? next : view;
}

/** Na een sprong: het hele gewenste bereik, zodra het getekende bereik er een deel van is; anders null. */
export function nextStep(shown: Range, wanted: Range): Range | null {
  if (shown.start === wanted.start && shown.end === wanted.end) return null;
  return overlaps(shown, wanted) ? wanted : null;
}

export function useWindowRows(o: {
  offsets: readonly number[];
  overscan: number;
  listRef: RefObject<HTMLElement | null>;
  keepIndex: number | null;
}): WindowRows {
  const { offsets, overscan, listRef, keepIndex } = o;
  const count = offsets.length - 1;
  const totalHeight = offsets[count] ?? 0;
  const [range, setRange] = useState<Range>(() => ({ start: 0, end: Math.min(count, 20) }));
  /** Het laatst gevraagde bereik (ook als de transition nog niet klaar is) */
  const wanted = useRef(range);
  const frame = useRef(0);

  const offsetOf = useCallback((i: number) => offsets[Math.max(0, Math.min(i, count))] ?? 0, [offsets, count]);

  const measure = useCallback(() => {
    const list = listRef.current;
    const listTop = list ? list.getBoundingClientRect().top + window.scrollY : 0;
    return { top: window.scrollY - listTop, viewport: window.innerHeight, listTop };
  }, [listRef]);

  const update = useCallback(() => {
    const { top, viewport } = measure();
    const next = variableRange(offsets, top, viewport, overscan);
    const old = wanted.current;
    if (old.start === next.start && old.end === next.end) return;
    wanted.current = next;
    if (overlaps(old, next)) setRange(next);
    else {
      const first = firstStep(old, next, variableRange(offsets, top, viewport, 0));
      startTransition(() => setRange(first));
    }
  }, [measure, offsets, overscan]);

  useLayoutEffect(update, [update]);

  // Na een sprong (alleen het beeld) de marge in het volgende frame
  useEffect(() => {
    if (nextStep(range, wanted.current) === null) return;
    const f = requestAnimationFrame(() => {
      const step = nextStep(range, wanted.current);
      if (step) startTransition(() => setRange(step));
    });
    return () => cancelAnimationFrame(f);
  }, [range]);

  useEffect(() => {
    const onScroll = () => {
      if (frame.current) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = 0;
        update();
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
  }, [update]);

  const scrollToIndex = useCallback(
    (i: number, align: "auto" | "start" | "center" = "auto") => {
      if (i < 0 || i >= count) return;
      const { top, viewport, listTop } = measure();
      const y = scrollTarget(offsetOf(i), offsetOf(i + 1), top, viewport, align);
      if (y === null) return;
      // De rijen volgen via het scroll-event (in een volgend frame, een eigen taak): zo is een
      // toets (PageDown) geen lange taak met zowel de focus als een stuk nieuwe rijen erin
      window.scrollTo({ top: Math.max(0, listTop + y) });
    },
    [count, measure, offsetOf],
  );

  const start = Math.min(range.start, count);
  const end = Math.min(range.end, count);
  return { start, end, totalHeight, offsetOf, scrollToIndex, extra: extraIndex({ start, end }, keepIndex, count) };
}
