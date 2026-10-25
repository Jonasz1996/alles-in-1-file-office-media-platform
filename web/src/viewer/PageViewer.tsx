// Pagina's onder elkaar, als afbeeldingen van de server (generiek: ook voor fase 4). Van tevoren
// staat er voor elke pagina een plaatshouder op de goede grootte (page_sizes); alleen wat in
// beeld is (plus twee) staat in de DOM. srcset kiest de breedte (640 .. 2560) die bij het
// scherm past. Passend op breedte of hele pagina, 50–400 %, een paginavak en een
// inhoudsopgave.

import { type JSX, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "../i18n";
import { IconButton } from "../ui/Button";
import { useShortcuts } from "../ui/keys";
import { prefixOffsets, variableRange } from "../ui/virtual";
import type { TocEntry } from "./api";

export interface PageViewerProps {
  count: number;
  /** Per pagina [breedte, hoogte] in punten */
  sizes: readonly (readonly [number, number])[];
  /** De breedtes die de server maakt */
  widths: readonly number[];
  /** Het adres van pagina n (vanaf 1) op breedte w */
  url(n: number, w: number): string;
  toc?: readonly TocEntry[];
  labels?: readonly string[] | null;
  /** Een pagina laadde niet (bijv. een verlopen token) */
  onPageError?(n: number): void;
}

const GAP = 16;
const PAD = 16;
/** 100 % = 96 pixels per inch (72 punten) */
const ACTUAL = 96 / 72;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;
const STEP = 1.25;

type Fit = "width" | "page" | number;

/** Pixels per punt bij deze stand. */
export function pageScale(fit: Fit, sizes: readonly (readonly [number, number])[], view: { w: number; h: number }): number {
  if (typeof fit === "number") return fit * ACTUAL;
  const maxW = Math.max(1, ...sizes.map((s) => s[0]));
  const maxH = Math.max(1, ...sizes.map((s) => s[1]));
  const byWidth = Math.max(1, view.w - 2 * PAD) / maxW;
  if (fit === "width") return byWidth;
  return Math.min(byWidth, Math.max(1, view.h - 2 * PAD) / maxH);
}

/** De pagina (vanaf 0) die op hoogte y begint of loopt. */
function pageAt(offsets: readonly number[], y: number): number {
  let lo = 0;
  let hi = offsets.length - 2;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid + 1] > y) hi = mid;
    else lo = mid + 1;
  }
  return Math.max(0, lo);
}

export default function PageViewer({ count, sizes, widths, url, toc = [], labels, onPageError }: PageViewerProps): JSX.Element {
  const scroller = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ w: 0, h: 0, top: 0 });
  const [fit, setFit] = useState<Fit>("width");
  const [tocOpen, setTocOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [, setFailures] = useState(0);
  // Pagina's die al geladen zijn, houden hun adres (ook als het token intussen vernieuwd is)
  // (Ook een pagina die niet laadde: die wordt pas opnieuw gevraagd met een nieuw adres, anders
  // zou een kapotte pagina eindeloos opnieuw laden.)
  const used = useRef(
    new Map<number, { src: string; srcSet: string; done: boolean; failed: boolean; from: PageViewerProps["url"] }>(),
  );
  const anchor = useRef<{ page: number; frac: number } | null>(null);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => setView((v) => ({ ...v, w: el.clientWidth, h: el.clientHeight }));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const k = pageScale(fit, sizes, view);
  const dims = useMemo(() => sizes.map(([w, h]) => [Math.round(w * k), Math.round(h * k)] as const), [sizes, k]);
  const offsets = useMemo(() => prefixOffsets(count, (i) => (dims[i]?.[1] ?? 0) + GAP), [count, dims]);
  const trackW = Math.max(view.w, Math.max(0, ...dims.map((d) => d[0])) + 2 * PAD);
  const range = variableRange(offsets, view.top - PAD, view.h, 2);
  const current = pageAt(offsets, Math.max(0, view.top - PAD) + view.h / 3);

  // Na zoomen dezelfde plek in beeld houden
  useLayoutEffect(() => {
    const el = scroller.current;
    const a = anchor.current;
    if (!el || !a) return;
    anchor.current = null;
    el.scrollTop = PAD + offsets[a.page] + a.frac * (offsets[a.page + 1] - offsets[a.page]);
  }, [k]);

  function setZoom(next: Fit) {
    const top = Math.max(0, view.top - PAD);
    const page = pageAt(offsets, top);
    const height = offsets[page + 1] - offsets[page] || 1;
    anchor.current = { page, frac: (top - offsets[page]) / height };
    setFit(next);
  }

  const percent = Math.round((k / ACTUAL) * 100);
  const zoomBy = (factor: number) => setZoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, (k / ACTUAL) * factor)));

  function goToPage(n: number) {
    const el = scroller.current;
    if (!el || n < 1 || n > count) return;
    el.scrollTop = PAD + offsets[n - 1];
    setView((v) => ({ ...v, top: el.scrollTop }));
  }

  useShortcuts(
    [
      { combo: "pagedown", run: () => goToPage(current + 2), label: t("viewer.keys.nextPage") },
      { combo: "pageup", run: () => goToPage(current), label: t("viewer.keys.prevPage") },
      { combo: ["+", "="], run: () => zoomBy(STEP), label: t("viewer.keys.zoomIn") },
      { combo: "-", run: () => zoomBy(1 / STEP), label: t("viewer.keys.zoomOut") },
      { combo: "0", run: () => setZoom("width"), label: t("viewer.keys.fitWidth") },
      { combo: "t", run: () => setTocOpen((v) => !v), when: () => toc.length > 0, label: t("viewer.keys.toc") },
    ],
    { scope: "viewer" },
  );

  // Een ander document. Niet bij een vernieuwd token: dan komt page_sizes als nieuwe array met
  // dezelfde maten, en de pagina's die er al staan houden hun adres (geen nieuwe download).
  const shape = useMemo(() => `${count}:${sizes.map((s) => s.join("x")).join(",")}`, [count, sizes]);
  useEffect(() => {
    used.current.clear();
  }, [shape]);

  function pageSource(n: number) {
    const hit = used.current.get(n);
    // Nog niet geladen en intussen een nieuw adres (token vernieuwd): dat nieuwe
    if (hit && (hit.done || hit.from === url)) return hit;
    const width = dims[n - 1]?.[0] ?? 640;
    const best = widths.find((w) => w >= width) ?? widths[widths.length - 1] ?? 1280;
    const made = {
      src: url(n, best),
      srcSet: widths.map((w) => `${url(n, w)} ${w}w`).join(", "),
      done: false,
      failed: false,
      from: url,
    };
    used.current.set(n, made);
    return made;
  }

  const pages: JSX.Element[] = [];
  for (let i = range.start; i < range.end; i++) {
    const n = i + 1;
    const [w, h] = dims[i] ?? [0, 0];
    const src = pageSource(n);
    pages.push(
      <div
        key={n}
        className="page"
        style={{ top: `${PAD + offsets[i]}px`, left: `${(trackW - w) / 2}px`, width: `${w}px`, height: `${h}px` }}
      >
        {src.failed ? (
          <p className="page-failed muted">{t("viewer.pdf.pageFailed", { n: labels?.[i] || n })}</p>
        ) : (
          <img
            src={src.src}
            srcSet={src.srcSet}
            sizes={`${w}px`}
            width={w}
            height={h}
            alt={t("viewer.pdf.pageAlt", { n: labels?.[i] || n })}
            decoding="async"
            onLoad={() => {
              src.done = true;
            }}
            onError={() => {
              src.failed = true;
              setFailures((f) => f + 1);
              onPageError?.(n);
            }}
          />
        )}
      </div>,
    );
  }

  const label = labels?.[current];
  return (
    <div className={`pages${tocOpen ? " with-toc" : ""}`}>
      <div className="pages-tools" role="toolbar" aria-label={t("viewer.pdf.tools")}>
        {toc.length > 0 && (
          <IconButton
            icon="list"
            className="ghost"
            label={t("viewer.pdf.toc")}
            pressed={tocOpen}
            onClick={() => setTocOpen((v) => !v)}
          />
        )}
        <label className="page-box">
          <span>{t("viewer.pdf.page")}</span>
          <input
            type="number"
            min={1}
            max={count}
            value={draft ?? String(current + 1)}
            onFocus={(e) => e.currentTarget.select()}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => setDraft(null)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                goToPage(Number(draft ?? current + 1));
                setDraft(null);
              }
            }}
          />
          <span>{t("viewer.pdf.of", { count })}</span>
          {label && label !== String(current + 1) && <span className="muted">({label})</span>}
        </label>
        <IconButton icon="zoom-out" className="ghost" label={t("viewer.pdf.zoomOut")} onClick={() => zoomBy(1 / STEP)} />
        <output className="zoom-percent">{t("viewer.image.percent", { n: percent })}</output>
        <IconButton icon="zoom-in" className="ghost" label={t("viewer.pdf.zoomIn")} onClick={() => zoomBy(STEP)} />
        <button type="button" className="btn ghost" aria-pressed={fit === "width"} onClick={() => setZoom("width")}>
          {t("viewer.pdf.fitWidth")}
        </button>
        <button type="button" className="btn ghost" aria-pressed={fit === "page"} onClick={() => setZoom("page")}>
          {t("viewer.pdf.fitPage")}
        </button>
      </div>
      <div className="pages-body">
        {tocOpen && (
          <nav className="pages-toc" aria-label={t("viewer.pdf.toc")}>
            <ul>
              {toc.map((entry, i) => (
                <li key={i} className={`toc-level-${Math.min(entry.level, 4)}`}>
                  <button
                    type="button"
                    className="link"
                    aria-current={entry.page === current + 1 ? "page" : undefined}
                    onClick={() => goToPage(entry.page)}
                  >
                    {entry.title || t("viewer.pdf.pageAlt", { n: entry.page })}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        )}
        <div
          ref={scroller}
          className="pages-scroll"
          tabIndex={0}
          role="region"
          aria-label={t("viewer.pdf.region")}
          onScroll={(e) => {
            const top = e.currentTarget.scrollTop;
            setView((v) => (v.top === top ? v : { ...v, top }));
          }}
        >
          <div className="pages-track" style={{ height: `${2 * PAD + offsets[count]}px`, width: `${trackW}px` }}>
            {pages}
          </div>
        </div>
      </div>
    </div>
  );
}
