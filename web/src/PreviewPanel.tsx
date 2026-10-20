import { useEffect, useRef, useState } from "react";
import { api, type FileNode, formatBytes, formatDate, type Preview, type PreviewMeta } from "./api";
import { category, type Category } from "./Thumb";

/** 83 -> "1:23", 3723 -> "1:02:03". */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

const DIALOG = {
  padding: 0,
  border: "1px solid var(--line)",
  borderRadius: 10,
  background: "var(--bg)",
  color: "var(--fg)",
  width: "min(56rem, 94vw)",
  maxHeight: "92vh",
} as const;
const FIT = { display: "block", maxWidth: "100%", maxHeight: "65vh", margin: "0 auto" } as const;

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

/**
 * Adres om af te spelen (de downloadlink met inline=true). Die verloopt na een paar minuten;
 * vraagt de speler later nog een stuk (doorspoelen) en mislukt dat, dan een nieuwe link en
 * verder waar hij was.
 */
function useMediaUrl(nodeId: string, wanted: boolean) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const resumeAt = useRef(0);
  const renewals = useRef({ count: 0, at: 0 });

  useEffect(() => {
    if (!wanted) return;
    let current = true;
    api
      .downloadUrl(nodeId, true)
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
      resumeAt.current = e.currentTarget.currentTime;
      api
        .downloadUrl(nodeId, true)
        .then(setUrl)
        .catch((err: Error) => setError(err.message));
    },
    onLoadedMetadata: (e: { currentTarget: HTMLMediaElement }) => {
      if (resumeAt.current) e.currentTarget.currentTime = resumeAt.current;
      resumeAt.current = 0;
    },
  };
  return { url, error, props };
}

export function PreviewPanel({
  node,
  preview,
  onClose,
  onDownload,
}: {
  node: FileNode;
  preview?: Preview;
  onClose: () => void;
  onDownload: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const kind = category(node);
  const meta = preview?.meta ?? {};
  const player = playerFor(kind, meta);
  const media = useMediaUrl(node.id, player !== null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Niet sluiten bij opruimen: dan zou het close-event het paneel meteen weer weghalen
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
  }, []);

  const close = () => dialog.current?.close(); // via het close-event naar onClose

  async function openInTab() {
    try {
      window.open(await api.downloadUrl(node.id, true), "_blank", "noopener");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  let body;
  if (player === "video") {
    body = media.url ? (
      <video controls playsInline preload="metadata" poster={preview?.large ?? undefined} style={FIT} {...media.props} />
    ) : (
      <p className="muted">Laden…</p>
    );
  } else if (player === "audio") {
    body = (
      <div style={{ display: "grid", gap: "0.8rem", justifyItems: "center" }}>
        {preview?.large && <img src={preview.large} alt="Hoes" style={{ ...FIT, maxHeight: "16rem" }} />}
        {(meta.title || meta.artist) && (
          <p style={{ margin: 0 }}>
            <strong>{meta.title ?? node.name}</strong>
            {meta.artist && <> · {meta.artist}</>}
            {meta.album && <span className="muted"> ({meta.album})</span>}
          </p>
        )}
        {media.url ? <audio controls preload="metadata" style={{ width: "100%" }} {...media.props} /> : <p>Laden…</p>}
      </div>
    );
  } else if (meta.text !== undefined) {
    body = (
      <pre
        style={{
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          maxHeight: "60vh",
          overflow: "auto",
          margin: 0,
          padding: "0.8rem",
          background: "var(--surface)",
          borderRadius: 6,
        }}
      >
        {meta.text}
        {meta.truncated && "\n…"}
      </pre>
    );
  } else if (meta.entries) {
    const more = (meta.count ?? meta.entries.length) - meta.entries.length;
    body = (
      <div className="table-wrap" style={{ maxHeight: "60vh", overflow: "auto" }}>
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
                <td style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{e.name}</td>
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
  } else if (preview?.large) {
    body = <img src={preview.large} alt={node.name} style={FIT} />;
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

  const details = [
    meta.width && meta.height ? `${meta.width} × ${meta.height}` : null,
    meta.duration ? formatDuration(meta.duration) : null,
    meta.pages ? (meta.pages === 1 ? "1 pagina" : `${meta.pages} pagina's`) : null,
    formatBytes(node.size),
    `gewijzigd ${formatDate(node.modified_at)}`,
  ].filter(Boolean);

  return (
    <dialog
      ref={dialog}
      className="preview-dialog"
      aria-label={node.name}
      style={DIALOG}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === dialog.current) close(); // klik naast het paneel
      }}
    >
      <style>{".preview-dialog::backdrop { background: rgb(0 0 0 / 0.6); }"}</style>
      <div style={{ padding: "1rem 1.2rem" }}>
        <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
          <h2 style={{ margin: 0, flex: 1, fontSize: "1.15rem", overflowWrap: "anywhere" }}>{node.name}</h2>
          <button type="button" onClick={close}>
            Sluiten
          </button>
        </div>
        <div style={{ margin: "1rem 0" }}>{body}</div>
        <p className="muted" style={{ margin: 0 }}>
          {details.join(" · ")}
        </p>
        <div className="toolbar" style={{ marginBottom: 0 }}>
          <button type="button" onClick={onDownload}>
            Downloaden
          </button>
          {kind === "pdf" && (
            <button type="button" onClick={openInTab}>
              Openen in nieuw tabblad
            </button>
          )}
        </div>
        {(error || media.error) && <p role="alert">{error ?? media.error}</p>}
      </div>
    </dialog>
  );
}
