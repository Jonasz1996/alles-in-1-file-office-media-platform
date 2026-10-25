// De uploadlade rechtsonder (P3): totalen, snelheid en resterende tijd; per bestand en voor
// alles Pauzeren, Hervatten en Annuleren; mislukte uploads met de reden, Opnieuw en
// Weggooien; "Alles tonen" in een gevirtualiseerde lijst; de vraag over dubbele bestanden; en
// de onderbroken uploads van na herladen. De schil (App.tsx) toont hem zolang er iets is.

import { type CSSProperties, type JSX, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { errorText, fmt, locale, t } from "../i18n";
import { Button, IconButton } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useShortcuts } from "../ui/keys";
import { announce, toast } from "../ui/toast";
import { VirtualList } from "../ui/virtual";
import {
  type UploadFailure,
  type UploadItem,
  type UploadSummary,
  uploadQueue,
  useUploadSummary,
  useUploads,
} from "../uploadQueue";
import { ConflictDialog } from "./ConflictDialog";
import { conflicts } from "./conflicts";
import { roundEta } from "./eta";
import { Interrupted } from "./Interrupted";
import { interrupted } from "./interrupted";
import "./upload.css";

/** Zoveel bestanden staan er in de lade zelf; de rest via "Alles tonen". */
const VISIBLE = 5;
const ROW_H = 52;

/** De tekst bij een mislukte upload. */
export function failureText(f: UploadFailure | undefined, fallback?: string): string {
  if (!f) return fallback ?? "";
  if (f.folder !== undefined && (f.code === "name_taken" || f.code === "invalid_name" || f.code === "too_deep")) {
    return t(`upload.folderError.${f.code}`, { folder: f.folder });
  }
  if (f.discard) return t("upload.discardFailed", { reason: errorText(f) });
  return errorText(f);
}

function etaText(seconds: number | null): string | null {
  if (seconds === null || seconds <= 0) return null;
  const r = roundEta(seconds);
  if (r.unit === "s") return t("upload.tray.etaSeconds", { n: r.value });
  if (r.unit === "min") return t("upload.tray.etaMinutes", { n: r.value });
  return t("upload.tray.etaHours", { h: r.value, m: r.minutes ?? 0 });
}

/** "1.234 van 5.000 · 2,1 GB van 6 GB · 12 MB/s · nog ~6 min" */
export function summaryText(s: UploadSummary): string {
  if (s.files === 0) return s.preparing ? t("upload.tray.preparing") : "";
  const parts = [
    t("upload.tray.files", { done: s.done, files: s.files }),
    t("upload.tray.bytes", { sent: fmt.bytes(Math.max(0, s.sent)), total: fmt.bytes(s.bytes) }),
  ];
  if (s.running && s.rate) parts.push(t("upload.tray.rate", { rate: fmt.bytes(s.rate) }));
  const eta = s.running ? etaText(s.eta) : null;
  if (eta) parts.push(eta);
  if (!s.running && !s.waiting && s.paused) parts.push(t("upload.tray.paused"));
  if (s.failed) parts.push(t("upload.tray.failed", { n: s.failed }));
  if (s.preparing) parts.push(t("upload.tray.preparing"));
  return parts.join(" · ");
}

/** De bestanden die in de lade zelf staan: eerst wat loopt, dan mislukt, gepauzeerd, wachtend. */
function visible(items: readonly UploadItem[]): UploadItem[] {
  const order: UploadItem["state"][] = ["running", "failed", "discarding", "paused", "waiting"];
  const buckets = new Map<UploadItem["state"], UploadItem[]>(order.map((s) => [s, []]));
  for (const item of items) {
    const bucket = buckets.get(item.state);
    if (bucket && bucket.length < VISIBLE) bucket.push(item);
  }
  return order.flatMap((s) => buckets.get(s) ?? []).slice(0, VISIBLE);
}

function UploadRow({
  item,
  style,
  inList = true,
}: {
  item: UploadItem;
  style?: CSSProperties;
  /** In een <ul> (de lade) een <li>; in de gevirtualiseerde lijst een div met role listitem */
  inList?: boolean;
}): JSX.Element {
  const name = item.path ?? item.file.name;
  const q = uploadQueue;
  let status: JSX.Element;
  let actions: JSX.Element;
  if (item.state === "failed") {
    status = (
      <span className="upload-reason" role="alert">
        {failureText(item.failure, item.error)}
      </span>
    );
    actions = (
      <>
        {item.retry !== false && (
          <Button aria-label={t("upload.item.retryNamed", { name })} onClick={() => q.retry(item.key)}>
            {t("upload.item.retry")}
          </Button>
        )}
        <Button aria-label={t("upload.item.discardNamed", { name })} onClick={() => void q.discard(item.key)}>
          {t("upload.item.discard")}
        </Button>
      </>
    );
  } else if (item.state === "discarding") {
    status = <span className="muted">{t("upload.item.discarding")}</span>;
    actions = <></>;
  } else {
    const label =
      item.state === "paused"
        ? t("upload.item.paused")
        : item.state === "waiting"
          ? t("upload.item.waiting")
          : fmt.percent(item.progress);
    status = (
      <>
        <progress max={1} value={item.progress} aria-label={name} />
        <span className="muted upload-state">{label}</span>
      </>
    );
    actions = (
      <>
        {item.state === "paused" ? (
          <Button aria-label={t("upload.item.resumeNamed", { name })} onClick={() => q.resume(item.key)}>
            {t("upload.item.resume")}
          </Button>
        ) : (
          <Button aria-label={t("upload.item.pauseNamed", { name })} onClick={() => q.pause(item.key)}>
            {t("upload.item.pause")}
          </Button>
        )}
        <Button aria-label={t("upload.item.cancelNamed", { name })} onClick={() => q.cancel(item.key)}>
          {t("upload.item.cancel")}
        </Button>
      </>
    );
  }
  const content = (
    <>
      <span className="upload-name" title={name}>
        {name}
      </span>
      <span className="upload-status">{status}</span>
      <span className="upload-actions">{actions}</span>
    </>
  );
  const className = `upload-row state-${item.state}`;
  return inList ? (
    <li className={className}>{content}</li>
  ) : (
    <div className={className} style={style} role="listitem">
      {content}
    </div>
  );
}

function AllUploads({ items, onClose }: { items: readonly UploadItem[]; onClose: () => void }): JSX.Element {
  const scroller = useRef<HTMLDivElement>(null);
  return (
    <Dialog title={t("upload.tray.allTitle")} onClose={onClose} size="l">
      <div ref={scroller} className="upload-all">
        <VirtualList
          count={items.length}
          get={(i) => items[i]}
          rowHeight={ROW_H}
          getKey={(item, i) => item?.key ?? String(i)}
          scroller={scroller}
          role="list"
          ariaLabel={t("upload.tray.allTitle")}
          renderRow={(item, _i, style) => (item ? <UploadRow item={item} style={style} inList={false} /> : null)}
        />
      </div>
    </Dialog>
  );
}

export default function UploadTray(): JSX.Element | null {
  const summary = useUploadSummary();
  const items = useUploads();
  const pending = useSyncExternalStore(interrupted.subscribe, interrupted.getSnapshot);
  const question = useSyncExternalStore(conflicts.subscribe, conflicts.current);
  const [open, setOpen] = useState(true);
  const [all, setAll] = useState(false);
  const active = summary.running + summary.waiting > 0;
  const pausable = active;
  const resumable = summary.paused > 0;

  // Weggaan terwijl er uploads lopen: de browser vraagt eerst of dat de bedoeling is
  useEffect(() => {
    if (!active && !summary.preparing) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active, summary.preparing]);

  // Alles klaar (zonder fouten): melden voor schermlezers
  const had = useRef(0);
  useEffect(() => {
    if (had.current > 0 && items.length === 0 && !summary.preparing) announce(t("upload.tray.done"));
    had.current = items.length;
  }, [items.length, summary.preparing]);

  useShortcuts(
    [
      {
        combo: "shift+p",
        label: t("upload.shortcuts.pauseAll"),
        when: () => pausable || resumable,
        run: () => (pausable ? uploadQueue.pauseAll() : uploadQueue.resumeAll()),
      },
    ],
    { scope: "global" },
  );

  const shown = visible(items);
  const text = summaryText(summary);
  const progress = summary.bytes ? Math.min(1, Math.max(0, summary.sent / summary.bytes)) : 0;

  return (
    <section className="upload-tray" aria-label={t("upload.tray.label")} data-open={open || undefined}>
      <header className="upload-tray-head">
        <h2 className="upload-tray-title">{t("upload.title")}</h2>
        <IconButton
          icon={open ? "chevron-down" : "chevron-up"}
          className="ghost"
          label={open ? t("upload.tray.collapse") : t("upload.tray.expand")}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        />
      </header>
      <p className="upload-tray-summary" aria-live="polite">
        {text}
      </p>
      {summary.files > 0 && <progress className="upload-total" max={1} value={progress} aria-hidden="true" />}
      {open && (
        <div className="upload-tray-body">
          {items.length > 0 && (
            <div className="upload-tray-actions">
              {pausable && <Button onClick={() => uploadQueue.pauseAll()}>{t("upload.tray.pauseAll")}</Button>}
              {resumable && <Button onClick={() => uploadQueue.resumeAll()}>{t("upload.tray.resumeAll")}</Button>}
              {(active || resumable) && (
                <Button onClick={() => uploadQueue.cancelAll()}>{t("upload.tray.cancelAll")}</Button>
              )}
              {summary.failed > 0 && (
                <Button onClick={() => uploadQueue.discardFailed()}>{t("upload.tray.discardFailed")}</Button>
              )}
            </div>
          )}
          {shown.length > 0 && (
            <ul className="upload-list">
              {shown.map((item) => (
                <UploadRow key={item.key} item={item} />
              ))}
            </ul>
          )}
          {items.length > VISIBLE && (
            <Button variant="link" onClick={() => setAll(true)}>
              {t("upload.tray.showAll", { n: items.length })}
            </Button>
          )}
          {pending.length > 0 && <Interrupted list={pending} />}
        </div>
      )}
      {all && <AllUploads items={items} onClose={() => setAll(false)} />}
      {question && (
        <ConflictDialog
          key={question.length + (question[0]?.path ?? "")}
          items={question}
          shortcuts={locale() === "en" ? { replace: "r", keep: "k", skip: "s" } : { replace: "v", keep: "b", skip: "o" }}
          onAnswer={(answer) => {
            conflicts.answer(answer);
            if (answer === null) toast(t("upload.tray.cancelled"));
          }}
        />
      )}
    </section>
  );
}
