// Een album achter een publieke link (/s/<token>, kind "album"): de foto's in het raster van de
// tijdlijn, en een eenvoudige weergave per foto met ←/→ en downloaden. Alles met de sleutel van
// de link (link.request, link.downloadUrl); de adressen van de thumbnails zijn al ondertekend.

import { type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PublicShare } from "../api";
import { errorText, fmt, t } from "../i18n";
import type { PublicLink } from "../publicApi";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { EmptyState } from "../ui/EmptyState";
import { useScope, useShortcuts } from "../ui/keys";
import { Skeleton } from "../ui/Spinner";
import { useColumns } from "../ui/virtual";
import { GAP, type GridControl, MIN_CELL, PhotoGrid } from "./Grid";
import { publicAlbumItems, type PublicPhotoItem } from "./publicAlbumApi";
import { NO_SELECTION } from "./range";
import { buildLayout } from "./timeline";
import "./photos.css";

export interface PublicAlbumProps {
  link: PublicLink;
  share: PublicShare;
}

export default function PublicAlbum({ link, share }: PublicAlbumProps): JSX.Element {
  useScope("public-album");
  const [items, setItems] = useState<PublicPhotoItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const control = useRef<GridControl | null>(null);
  const { columns, cellWidth } = useColumns(wrap, MIN_CELL, GAP);
  const rowHeight = Math.max(MIN_CELL / 2, Math.round(cellWidth)) + GAP;

  useEffect(() => {
    let live = true;
    publicAlbumItems(link, (got) => live && setItems(got)).catch(
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [link]);

  const count = items?.length ?? 0;
  const layout = useMemo(() => buildLayout([count], columns, rowHeight, 0, false), [count, columns, rowHeight]);
  const section = useCallback(() => items ?? undefined, [items]);
  const noop = useCallback(() => {}, []);

  const show = useCallback((i: number) => {
    setFocus(i);
    setOpen(i);
  }, []);

  useShortcuts(
    [
      {
        combo: "enter",
        label: t("photos.keys.open"),
        run: () => show(focus),
        when: () => !!document.activeElement?.closest?.(".photo-grid"),
      },
    ],
    { scope: "public-album" },
  );

  if (error) return <p role="alert">{error}</p>;
  return (
    <div className="public-album">
      {items && <p className="muted">{t("photos.count", { n: count })}</p>}
      <div ref={wrap} className="photos-wrap">
        {items === null && <Skeleton />}
        {items && count === 0 && <EmptyState icon="album" title={t("photos.albumPage.empty")} />}
        {items && count > 0 && (
          <PhotoGrid
            layout={layout}
            rowHeight={rowHeight}
            headerHeight={0}
            section={section}
            selection={NO_SELECTION}
            focus={focus}
            setFocus={setFocus}
            label={t("photos.albumPage.grid", { name: share.name })}
            onVisible={noop}
            onCellClick={(i) => show(i)}
            onShiftMove={noop}
            control={control}
          />
        )}
      </div>
      {open !== null && items?.[open] && (
        <PhotoView
          link={link}
          items={items}
          index={open}
          onIndex={(i) => show(i)}
          onClose={() => {
            const at = open;
            setOpen(null);
            control.current?.scrollToItem(at, "center");
            control.current?.focusItem(at);
          }}
        />
      )}
    </div>
  );
}

function PhotoView({
  link,
  items,
  index,
  onIndex,
  onClose,
}: {
  link: PublicLink;
  items: PublicPhotoItem[];
  index: number;
  onIndex(i: number): void;
  onClose(): void;
}): JSX.Element {
  const it = items[index];
  const [video, setVideo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setVideo(null);
    if (it.media !== "video") return;
    let live = true;
    link
      .downloadUrl(it.id, true)
      .then((u) => live && setVideo(u))
      .catch((e: unknown) => live && setError(errorText(e)));
    return () => {
      live = false;
    };
  }, [link, it]);

  const go = (step: 1 | -1) => {
    const next = index + step;
    if (next >= 0 && next < items.length) onIndex(next);
  };

  async function download() {
    try {
      const a = document.createElement("a");
      a.href = await link.downloadUrl(it.id);
      a.download = it.name;
      a.click();
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <Dialog
      title={it.name}
      size="l"
      onClose={onClose}
      actions={
        <>
          <Button icon="chevron-left" disabled={index === 0} onClick={() => go(-1)}>
            {t("photos.view.prev")}
          </Button>
          <Button icon="chevron-right" disabled={index >= items.length - 1} onClick={() => go(1)}>
            {t("photos.view.next")}
          </Button>
          <Button icon="download" variant="primary" data-autofocus="" onClick={() => void download()}>
            {t("photos.download")}
          </Button>
        </>
      }
    >
      <ViewKeys onStep={go} />
      <figure className="photo-view">
        {it.media === "video" ? (
          video && <video src={video} controls poster={it.large ?? it.thumb ?? undefined} />
        ) : (
          <img src={it.large ?? it.thumb ?? undefined} alt={it.name} />
        )}
        <figcaption className="muted">
          {fmt.wallClock(it.taken_at)} · {t("photos.view.position", { n: index + 1, total: items.length })}
        </figcaption>
      </figure>
      {error && <p role="alert">{error}</p>}
    </Dialog>
  );
}

/** ←/→ in de weergave: een scope boven die van de dialoog. */
function ViewKeys({ onStep }: { onStep(step: 1 | -1): void }): null {
  useScope("public-view");
  useShortcuts(
    [
      { combo: "arrowleft", label: t("photos.view.prev"), run: () => onStep(-1) },
      { combo: "arrowright", label: t("photos.view.next"), run: () => onStep(1) },
    ],
    { scope: "public-view" },
  );
  return null;
}
