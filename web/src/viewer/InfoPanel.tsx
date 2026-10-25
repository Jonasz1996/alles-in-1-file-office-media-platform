// Het paneel met gegevens (toets I): het bestand, wat de server in de inhoud vond (afmetingen,
// duur, codecs, codering) en bij een foto Camera, Objectief, Belichting en Datum. Nooit een
// locatie: die hoort bij fase 6.

import type { JSX, ReactNode } from "react";
import { formatBytes } from "../api";
import { fmt, t } from "../i18n";
import { IconButton } from "../ui/Button";
import type { Exif, ViewerDetail } from "./api";
import { formatDuration, type ViewKind } from "./media";
import type { ViewerItem } from "./types";

function num(v: number | string | undefined): number | null {
  if (v === undefined || v === null || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** "f/2,8 · 1/250 s · ISO 100 · 35 mm" */
export function exposureText(exif: Exif): string {
  const parts: string[] = [];
  const f = num(exif.fnumber);
  if (f !== null) parts.push(`f/${fmt.number(f, { maximumFractionDigits: 1 })}`);
  else if (typeof exif.fnumber === "string" && exif.fnumber) parts.push(exif.fnumber);
  const time = num(exif.exposure);
  if (time !== null && time > 0) {
    parts.push(time < 1 ? `1/${Math.round(1 / time)} s` : `${fmt.number(time, { maximumFractionDigits: 1 })} s`);
  } else if (typeof exif.exposure === "string" && exif.exposure) parts.push(exif.exposure);
  if (exif.iso !== undefined && exif.iso !== "") parts.push(`ISO ${exif.iso}`);
  const focal = num(exif.focal);
  if (focal !== null) parts.push(`${fmt.number(focal, { maximumFractionDigits: 1 })} mm`);
  const bias = num(exif.bias);
  if (bias) parts.push(`${bias > 0 ? "+" : ""}${fmt.number(bias, { maximumFractionDigits: 1 })} EV`);
  return parts.join(" · ");
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  if (children === null || children === undefined || children === "") return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export default function InfoPanel({
  item,
  detail,
  kind,
  onClose,
}: {
  item: ViewerItem;
  detail: ViewerDetail | undefined;
  kind: ViewKind;
  onClose(): void;
}): JSX.Element {
  const meta = detail?.meta ?? {};
  const exif = meta.exif ?? {};
  const camera = [exif.make, exif.model].filter(Boolean).join(" ");
  const exposure = exposureText(exif);
  const isPhoto = kind === "image" || !!detail?.taken_at || !!camera;
  const codecs = [
    meta.video?.codec && `${meta.video.codec}${meta.video.profile ? ` (${meta.video.profile})` : ""}`,
    meta.audio?.codec &&
      `${meta.audio.codec}${meta.audio.channels ? ` · ${t("viewer.info.channels", { n: meta.audio.channels })}` : ""}`,
  ]
    .filter(Boolean)
    .join(" / ");

  return (
    <aside className="viewer-info" aria-label={t("viewer.info.title")}>
      <header>
        <h3>{t("viewer.info.title")}</h3>
        <IconButton icon="close" className="ghost" label={t("viewer.info.close")} onClick={onClose} />
      </header>
      <dl>
        <Row label={t("viewer.info.name")}>{item.name}</Row>
        {item.version && <Row label={t("viewer.info.version")}>{item.version.label}</Row>}
        <Row label={t("viewer.info.type")}>{item.mime ?? t("viewer.info.unknown")}</Row>
        <Row label={t("viewer.info.size")}>{formatBytes(item.size)}</Row>
        <Row label={t("viewer.info.modified")}>{fmt.dateTime(item.modified_at)}</Row>
        <Row label={t("viewer.info.dimensions")}>
          {meta.width && meta.height ? t("viewer.info.pixels", { w: meta.width, h: meta.height }) : null}
        </Row>
        <Row label={t("viewer.info.duration")}>{meta.duration ? formatDuration(meta.duration) : null}</Row>
        <Row label={t("viewer.info.pages")}>{meta.pages ? String(meta.pages) : null}</Row>
        <Row label={t("viewer.info.codecs")}>{codecs || null}</Row>
        <Row label={t("viewer.info.container")}>{meta.container ? meta.container.split(",")[0] : null}</Row>
        <Row label={t("viewer.info.encoding")}>{meta.encoding ?? null}</Row>
        <Row label={t("viewer.info.titleTag")}>{kind === "audio" || meta.office ? (meta.title ?? null) : null}</Row>
        <Row label={t("viewer.info.artist")}>{meta.artist ?? meta.author ?? null}</Row>
        <Row label={t("viewer.info.album")}>{meta.album ?? null}</Row>
      </dl>
      {isPhoto && (camera || exif.lens || exposure || detail?.taken_at) && (
        <section>
          <h4>{t("viewer.info.photo")}</h4>
          <dl>
            <Row label={t("viewer.info.camera")}>{camera || null}</Row>
            <Row label={t("viewer.info.lens")}>{exif.lens ?? null}</Row>
            <Row label={t("viewer.info.exposure")}>{exposure || null}</Row>
            <Row label={t("viewer.info.date")}>{detail?.taken_at ? fmt.wallClock(detail.taken_at) : null}</Row>
          </dl>
        </section>
      )}
    </aside>
  );
}
