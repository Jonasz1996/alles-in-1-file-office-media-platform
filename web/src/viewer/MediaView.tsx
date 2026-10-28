// Video en audio met de speler van de browser. Altijd proberen af te spelen: de codecs die de
// server las, zijn alleen een hint. Lukt het niet, dan zoeken we uit waarom: kan de browser het
// formaat niet aan, dan zeggen we dat (met downloaden); was de link verlopen of de verbinding
// weg, dan één nieuwe link en verder waar hij was.
//
// De haak van fase 5 (M0, frozen; Keuze 32): een video laadt de speler van M6
// (player/ViewerPlayer.tsx, een eigen chunk) als je ingelogd bent, het de huidige versie is en
// media aan staat (wantsPlayer in media/index.ts). De speler krijgt de <video> van hieronder als
// fallback; tot M6 toont hij alleen die. Zonder media, via een publieke link en voor audio blijft
// het precies fase 3. De keuze valt één keer per bestand (de viewer geeft elk bestand een eigen
// key): nooit midden in het afspelen wisselen.

import { Component, type JSX, lazy, type ReactNode, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { errorText, t } from "../i18n";
import { mediaStatus, wantsPlayer } from "../media";
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

/** Wat de speler van M6 in de viewer krijgt (het contract van de haak; frozen na M0). */
export interface ViewerPlayerProps {
  item: ViewerItem;
  detail: ViewerDetail;
  /** Altijd een ingelogde bron (kind "user"), en nooit een eerdere versie */
  source: Source;
  /** De <video> van fase 3: voor als de speler het niet kan, of (tot M6) altijd */
  fallback: () => JSX.Element;
}

const ViewerPlayer = lazy(() => import("../player/ViewerPlayer"));

/** Lukt het chunk van de speler niet (een nieuwe versie uitgerold, geen verbinding) of faalt de
 * speler zelf: dan de <video> van fase 3, nooit een lege viewer. De fout gaat naar console.error
 * (zoals ChunkBoundary in App.tsx), zodat de bewaking van de e2e hem ziet. */
class PlayerBoundary extends Component<{ fallback: () => JSX.Element; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(error);
  }

  render() {
    return this.state.failed ? this.props.fallback() : this.props.children;
  }
}

export default function MediaView(props: Props): JSX.Element {
  const { item, detail, source, kind } = props;
  // Eén keer per bestand; get() vraagt een oude status op de achtergrond opnieuw
  const [player] = useState(() =>
    wantsPlayer(kind, source.kind, item.version !== undefined, source.kind === "user" ? mediaStatus.get() : null),
  );
  const native = () => <NativeMedia {...props} />;
  if (!player) return native();
  return (
    <PlayerBoundary fallback={native}>
      <Suspense
        fallback={
          <div className="media media-video">
            <Spinner label={t("viewer.media.loadingPlayer")} />
          </div>
        }
      >
        <ViewerPlayer item={item} detail={detail} source={source} fallback={native} />
      </Suspense>
    </PlayerBoundary>
  );
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

/** De speler van de browser (fase 3), met de sneltoetsen Spatie, K, J, L en M. */
function NativeMedia({ item, detail, source, kind, onEnded }: Props): JSX.Element {
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
