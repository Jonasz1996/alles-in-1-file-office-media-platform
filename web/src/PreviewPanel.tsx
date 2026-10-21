import { useEffect, useRef, useState } from "react";
import { api, type FileNode, formatBytes, formatDate, type Preview, type PreviewMeta } from "./api";
import { category, type Category } from "./Thumb";
import "./viewer/preview.css";

/** 83 -> "1:23", 3723 -> "1:02:03". */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/**
 * Speler voor video of audio, tenzij de server in de inhoud iets anders vond (tekst of een
 * archief): het soort komt nu nog van de naam, en die kan ernaast zitten.
 */
export function playerFor(kind: Category, meta: PreviewMeta): "video" | "audio" | null {
  if (meta.text !== undefined || meta.entries !== undefined) return null;
  return kind === "video" || kind === "audio" ? kind : null;
}

const RENEW_QUIET = 60_000;

/**
 * Mag de speler na een fout een nieuwe link? Hooguit drie kort na elkaar (dan is het bestand
 * zelf het probleem), maar een link die na lang afspelen verliep, mag altijd weer.
 */
export function mayRenew(state: { count: number; at: number }, now: number): boolean {
  if (now - state.at > RENEW_QUIET) state.count = 0;
  if (state.count >= 3) return false;
  state.count++;
  state.at = now;
  return true;
}

/** Waar de inhoud vandaan komt: een downloadadres, met inline=true om te tonen of af te spelen. */
export type UrlSource = (inline: boolean) => Promise<string>;

/** Wat de preview van een map of bestand laat zien (ook bij een publieke link). */
export type Shown = Pick<FileNode, "id" | "name" | "kind" | "mime" | "size" | "modified_at">;

/**
 * Adres om af te spelen (de downloadlink met inline=true). Die verloopt na een paar minuten;
 * vraagt de speler later nog een stuk (doorspoelen) en mislukt dat, dan een nieuwe link en
 * verder waar hij was.
 */
function useMediaUrl(nodeId: string, source: UrlSource, wanted: boolean) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const resumeAt = useRef(0);
  const renewals = useRef({ count: 0, at: 0 });
  const latest = useRef(source);
  useEffect(() => {
    latest.current = source;
  });

  useEffect(() => {
    if (!wanted) return;
    let current = true;
    latest
      .current(true)
      .then((u) => current && setUrl(u))
      .catch((e: Error) => current && setError(e.message));
    return () => {
      current = false;
    };
  }, [nodeId, wanted]);

  const props = {
    src: url ?? undefined,
    onError: (e: { currentTarget: HTMLMediaElement }) => {
      if (!mayRenew(renewals.current, Date.now()))
        return setError("Afspelen lukt niet. Probeer het bestand te downloaden.");
      const media = e.currentTarget;
      resumeAt.current = media.currentTime;
      latest
        .current(true)
        .then((renewed) => {
          // Hetzelfde adres (vlak na het vorige gemaakt, of de sleutel van een publieke link):
          // dan laadt de speler niet vanzelf opnieuw, en komt er ook geen nieuwe fout
          if (renewed === url) media.load();
          else setUrl(renewed);
        })
        .catch((err: Error) => setError(err.message));
    },
    onLoadedMetadata: (e: { currentTarget: HTMLMediaElement }) => {
      if (resumeAt.current) e.currentTarget.currentTime = resumeAt.current;
      resumeAt.current = 0;
    },
  };
  return { url, error, props };
}

/** Een afbeelding zelf (het origineel): als er geen preview is en er ook geen komt, of altijd
 * (always). Lukt dat niet (bijv. een soort die de browser niet kent), dan de preview. */
function useOriginal(node: Shown, preview: Preview | undefined, source: UrlSource, always: boolean) {
  const wanted =
    category(node) === "image" &&
    (always || (!preview?.large && (preview?.state === "none" || preview?.state === "failed")));
  const [url, setUrl] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  const latest = useRef(source);
  useEffect(() => {
    latest.current = source;
  });
  useEffect(() => {
    if (!wanted) return;
    let current = true;
    latest
      .current(true)
      .then((u) => current && setUrl(u))
      .catch(() => current && setBroken(true));
    return () => {
      current = false;
    };
  }, [node.id, wanted]);
  return wanted && !broken ? { url, onError: () => setBroken(true) } : null;
}

/** De preview zelf: speler, tekst, inhoud van een archief of een afbeelding. original: bij een
 * afbeelding het bestand zelf tonen in plaats van de (kleinere) preview. */
export function PreviewBody({
  node,
  preview,
  source,
  original: always = false,
}: {
  node: Shown;
  preview?: Preview;
  source: UrlSource;
  original?: boolean;
}) {
  const kind = category(node);
  const meta = preview?.meta ?? {};
  const player = playerFor(kind, meta);
  const media = useMediaUrl(node.id, source, player !== null);
  const original = useOriginal(node, preview, source, always);

  let body;
  if (player === "video") {
    body = media.url ? (
      <video
        controls
        playsInline
        preload="metadata"
        poster={preview?.large ?? undefined}
        className="preview-fit"
        {...media.props}
      />
    ) : (
      <p className="muted">Laden…</p>
    );
  } else if (player === "audio") {
    body = (
      <div className="preview-audio">
        {preview?.large && <img src={preview.large} alt="Hoes" className="preview-fit cover" />}
        {(meta.title || meta.artist) && (
          <p>
            <strong>{meta.title ?? node.name}</strong>
            {meta.artist && <> · {meta.artist}</>}
            {meta.album && <span className="muted"> ({meta.album})</span>}
          </p>
        )}
        {media.url ? <audio controls preload="metadata" {...media.props} /> : <p>Laden…</p>}
      </div>
    );
  } else if (meta.text !== undefined) {
    body = (
      <pre className="preview-text">
        {meta.text}
        {meta.truncated && "\n…"}
      </pre>
    );
  } else if (meta.entries) {
    const more = (meta.count ?? meta.entries.length) - meta.entries.length;
    body = (
      <div className="table-wrap preview-entries">
        <table>
          <thead>
            <tr>
              <th>Naam</th>
              <th>Grootte</th>
            </tr>
          </thead>
          <tbody>
            {meta.entries.map((e, i) => (
              // Op volgorde: een naam kan in een archief dubbel voorkomen
              <tr key={i}>
                <td className="name">{e.name}</td>
                <td>{e.name.endsWith("/") ? "" : formatBytes(e.size)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(more > 0 || meta.truncated) && (
          <p className="muted">{more > 0 ? `En nog ${more} andere.` : "En nog meer."}</p>
        )}
      </div>
    );
  } else if (original?.url) {
    body = <img src={original.url} alt={node.name} className="preview-fit" onError={original.onError} />;
  } else if (preview?.large) {
    body = <img src={preview.large} alt={node.name} className="preview-fit" />;
  } else if (original) {
    body = <p className="muted">Laden…</p>;
  } else if (!preview || preview.state === "pending") {
    body = <p className="muted">Het voorbeeld wordt gemaakt…</p>;
  } else {
    body = (
      <p className="muted">
        {preview.state === "failed"
          ? "Het voorbeeld kon niet worden gemaakt."
          : "Van dit soort bestand is er (nog) geen voorbeeld."}
      </p>
    );
  }

  return (
    <>
      {body}
      {media.error && <p role="alert">{media.error}</p>}
    </>
  );
}

/** "1920 × 1080 · 2,1 MB · gewijzigd …" onder de preview. */
export function previewDetails(node: Shown, meta: PreviewMeta): string {
  return [
    meta.width && meta.height ? `${meta.width} × ${meta.height}` : null,
    meta.duration ? formatDuration(meta.duration) : null,
    meta.pages ? (meta.pages === 1 ? "1 pagina" : `${meta.pages} pagina's`) : null,
    formatBytes(node.size),
    `gewijzigd ${formatDate(node.modified_at)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function PreviewPanel({
  node,
  preview,
  onClose,
  onDownload,
  source,
}: {
  node: Shown;
  preview?: Preview;
  onClose: () => void;
  onDownload: () => void;
  /** Standaard de downloadlink van een ingelogde gebruiker; een publieke link geeft zijn eigen. */
  source?: UrlSource;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const kind = category(node);
  const [error, setError] = useState<string | null>(null);
  const urls: UrlSource = source ?? ((inline) => api.downloadUrl(node.id, inline));

  useEffect(() => {
    // Niet sluiten bij opruimen: dan zou het close-event het paneel meteen weer weghalen
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
  }, []);

  const close = () => dialog.current?.close(); // via het close-event naar onClose

  async function openInTab() {
    try {
      window.open(await urls(true), "_blank", "noopener");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <dialog
      ref={dialog}
      className="preview-dialog"
      aria-label={node.name}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === dialog.current) close(); // klik naast het paneel
      }}
    >
      <div className="preview-inner">
        <div className="preview-head">
          <h2>{node.name}</h2>
          <button type="button" onClick={close}>
            Sluiten
          </button>
        </div>
        <div className="preview-body">
          <PreviewBody node={node} preview={preview} source={urls} />
        </div>
        <p className="muted preview-details">{previewDetails(node, preview?.meta ?? {})}</p>
        <div className="toolbar">
          <button type="button" onClick={onDownload}>
            Downloaden
          </button>
          {kind === "pdf" && (
            <button type="button" onClick={openInTab}>
              Openen in nieuw tabblad
            </button>
          )}
        </div>
        {error && <p role="alert">{error}</p>}
      </div>
    </dialog>
  );
}
