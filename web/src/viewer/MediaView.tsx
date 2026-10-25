// Video en audio met de speler van de browser. Altijd proberen af te spelen: de codecs die de
// server las, zijn alleen een hint. Lukt het niet, dan zoeken we uit waarom: kan de browser het
// formaat niet aan, dan zeggen we dat (met downloaden); was de link verlopen of de verbinding
// weg, dan één nieuwe link en verder waar hij was.

import { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { useShortcuts } from "../ui/keys";
import { Spinner } from "../ui/Spinner";
import type { ViewerDetail } from "./api";
import { codecLabel, type MediaProblem, mayRenew, mediaProblem } from "./media";
import type { Source } from "./source";
import type { ViewerItem } from "./types";

const SEEK = 10;

interface Props {
  item: ViewerItem;
  detail: ViewerDetail;
  source: Source;
  kind: "video" | "audio";
  /** Audio: na het einde het volgende bestand */
  onEnded?: () => void;
}

/** De status van een los verzoek naar het adres (alleen de eerste byte). */
async function probe(url: string): Promise<number> {
  try {
    const res = await fetch(url, { headers: { Range: "bytes=0-0" } });
    void res.body?.cancel();
    return res.status;
  } catch {
    return 0;
  }
}

export default function MediaView({ item, detail, source, kind, onEnded }: Props): JSX.Element {
  const media = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [problem, setProblem] = useState<MediaProblem>(null);
  const [error, setError] = useState<string | null>(null);
  const renewals = useRef({ count: 0, at: 0 });
  const resumeAt = useRef(0);
  const meta = detail.meta;

  useEffect(() => {
    let live = true;
    source.url(item, true).then(
      (u) => live && setUrl(u),
      (e: unknown) => live && setError(errorText(e)),
    );
    return () => {
      live = false;
    };
  }, [item, source]);

  const onError = useCallback(async () => {
    const el = media.current;
    if (!el || !url) return;
    const code = el.error?.code;
    const status = code === 2 || code === 4 ? await probe(url) : null;
    const found = mediaProblem(code, status);
    if (found === null) return;
    if ((found === "expired" || found === "network") && mayRenew(renewals.current, Date.now())) {
      resumeAt.current = el.currentTime;
      source.forget(item);
      try {
        const fresh = await source.url(item, true);
        // Hetzelfde adres (de sleutel van een publieke link): dan zelf opnieuw laden
        if (fresh === url) el.load();
        else setUrl(fresh);
      } catch (e) {
        setError(errorText(e));
      }
      return;
    }
    setProblem(found === "expired" ? "network" : found);
  }, [url, item, source]);

  const toggle = () => {
    const el = media.current;
    if (!el) return;
    if (el.paused) el.play().catch(() => {});
    else el.pause();
  };
  const seek = (by: number) => {
    const el = media.current;
    if (el) el.currentTime = Math.max(0, Math.min(el.duration || Infinity, el.currentTime + by));
  };
  useShortcuts(
    [
      { combo: [" ", "k"], run: toggle, label: t("viewer.keys.play") },
      { combo: "j", run: () => seek(-SEEK), label: t("viewer.keys.back10") },
      { combo: "l", run: () => seek(SEEK), label: t("viewer.keys.forward10") },
      {
        combo: "m",
        run: () => {
          if (media.current) media.current.muted = !media.current.muted;
        },
        label: t("viewer.keys.mute"),
      },
    ],
    { scope: "viewer", enabled: !problem },
  );

  async function download() {
    try {
      const a = document.createElement("a");
      a.href = await source.url(item, false);
      a.download = item.name;
      a.click();
    } catch (e) {
      setError(errorText(e));
    }
  }

  if (problem) {
    const codecs = codecLabel(meta);
    return (
      <div className="viewer-message">
        {detail.large && <img src={detail.large} alt="" className="media-poster" />}
        <p role="alert">{problem === "unsupported" ? t("viewer.media.unsupported") : t("viewer.media.network")}</p>
        {codecs && <p className="muted">{t("viewer.media.codecs", { codecs })}</p>}
        <Button icon="download" variant="primary" onClick={download}>
          {t("viewer.download")}
        </Button>
      </div>
    );
  }

  const common = {
    ref: media,
    src: url ?? undefined,
    controls: true,
    autoPlay: true,
    preload: "metadata" as const,
    onError: () => void onError(),
    onLoadedMetadata: () => {
      const el = media.current;
      if (el && resumeAt.current) el.currentTime = resumeAt.current;
      resumeAt.current = 0;
    },
    onEnded: kind === "audio" ? onEnded : undefined,
  };

  return (
    <div className={`media media-${kind}`}>
      {!url && !error && <Spinner label={t("viewer.loading")} />}
      {kind === "video" ? (
        url && <video {...common} playsInline poster={detail.large ?? undefined} className="media-video" />
      ) : (
        <div className="media-audio">
          {detail.large ? (
            <img src={detail.large} alt={t("viewer.media.cover")} className="media-cover" />
          ) : (
            <div className="media-cover placeholder" aria-hidden="true" />
          )}
          {(meta.title || meta.artist) && (
            <p className="media-tags">
              <strong>{meta.title ?? item.name}</strong>
              {meta.artist && <span> · {meta.artist}</span>}
              {meta.album && <span className="muted"> ({meta.album})</span>}
            </p>
          )}
          {url && <audio {...common} />}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
