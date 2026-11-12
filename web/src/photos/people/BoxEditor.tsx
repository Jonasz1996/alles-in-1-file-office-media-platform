// Een kader tekenen op een foto (Keuze 37): met de muis (slepen over de foto) en met het
// toetsenbord (de pijlen verplaatsen, Shift met een pijl maakt groter of kleiner; zie box.ts). Het
// vlak krijgt de focus met Tab; wat het kader nu is, hoort een schermlezer via een live-regio. De
// pijlen gaan niet naar de viewer eromheen (preventDefault): die bladert er anders mee.

import { type JSX, type KeyboardEvent, type PointerEvent, useId, useRef } from "react";
import { t } from "../../i18n";
import { boxFromPoints, boxKey, boxPercent } from "./box";
import type { Box } from "./types";

export function BoxEditor({ src, box, onChange }: { src: string | null; box: Box; onChange(b: Box): void }): JSX.Element {
  const id = useId();
  const area = useRef<HTMLDivElement>(null);
  const start = useRef<[number, number] | null>(null);

  function point(e: PointerEvent<HTMLDivElement>): [number, number] {
    const r = area.current?.getBoundingClientRect();
    if (!r || r.width === 0 || r.height === 0) return [0, 0];
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const next = boxKey(box, e.key, e.shiftKey);
    if (!next) return;
    e.preventDefault();
    e.stopPropagation();
    onChange(next);
  }

  const [x, y, w, h] = box;
  return (
    <div className="box-editor">
      <div
        ref={area}
        className="box-area"
        role="application"
        tabIndex={0}
        aria-label={t("photos_people.row.box")}
        aria-describedby={`${id}-help ${id}-nu`}
        onKeyDown={onKeyDown}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.preventDefault();
          e.currentTarget.focus();
          e.currentTarget.setPointerCapture?.(e.pointerId);
          start.current = point(e);
        }}
        onPointerMove={(e) => {
          const a = start.current;
          if (!a) return;
          const [bx, by] = point(e);
          onChange(boxFromPoints(a[0], a[1], bx, by));
        }}
        onPointerUp={() => {
          start.current = null;
        }}
        onPointerCancel={() => {
          start.current = null;
        }}
      >
        {src ? <img src={src} alt="" draggable={false} /> : <span className="box-blank" />}
        <span
          className="box-frame"
          style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }}
        />
      </div>
      <small id={`${id}-help`} className="muted">
        {t("photos_people.row.boxHelp")}
      </small>
      <p id={`${id}-nu`} className="sr-only" aria-live="polite">
        {t("photos_people.row.boxNow", boxPercent(box))}
      </p>
    </div>
  );
}
