// De pagina achter een publieke link (/s/<token>). Werkt zonder in te loggen: deze pagina
// gebruikt alleen de publieke API (publicApi.ts), nooit de login van de app.

import {
  type DragEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { formatBytes, formatDate, type Preview, type PublicListing, type PublicNode, type PublicShare } from "./api";
import { PreviewBody, PreviewPanel, previewDetails } from "./PreviewPanel";
import { type LinkProblem, linkProblem, PublicLink } from "./publicApi";
import { expiryLabel } from "./sharing";
import { category, Thumb } from "./Thumb";
import { UploadQueue } from "./uploadQueue";
import "./files.css";
import "./share.css";

const BATCH = 500;
/** Zo vaak (elke 3 s) vragen we een preview die nog gemaakt wordt opnieuw. */
const PREVIEW_ROUNDS = 20;

export function Public({ token }: { token: string | null }) {
  const link = useMemo(() => (token ? new PublicLink(token) : null), [token]);
  // Uploads lopen door als de pagina even om het wachtwoord vraagt; daarna kun je verder
  const queue = useMemo(() => (link ? new UploadQueue(link, { send: link.send }) : null), [link]);
  const [share, setShare] = useState<PublicShare | null>(() => link?.share ?? null);
  const [problem, setProblem] = useState<LinkProblem | null>(() =>
    token ? null : { kind: "gone", message: "Deze link bestaat niet (meer)" },
  );
  const [busy, setBusy] = useState(false);
  const started = useRef(false);

  const open = useCallback(
    async (password?: string) => {
      if (!link) return;
      setBusy(true);
      try {
        setShare(await link.open(password));
        setProblem(null);
      } catch (e) {
        setShare(null);
        setProblem(linkProblem(e));
      } finally {
        setBusy(false);
      }
    },
    [link],
  );

  // Werkt de sleutel niet meer (wachtwoord veranderd, link weg): dat tonen. Als layout-effect,
  // zodat het er al is voordat de onderdelen hieronder hun eerste verzoek doen.
  useLayoutEffect(() => {
    if (!link) return;
    link.onLocked = (e) => {
      setShare(null);
      setProblem(linkProblem(e));
    };
    return () => {
      link.onLocked = () => {};
    };
  }, [link]);

  useEffect(() => {
    // Een sleutel van eerder in dit tabblad: niet opnieuw om het wachtwoord vragen. Eén keer
    // openen, ook als React het effect twee keer draait (StrictMode)
    if (link && !link.share && !started.current) {
      started.current = true;
      open();
    }
  }, [link, open]);

  useEffect(() => {
    document.title = share ? `${share.name} · gedeeld` : "Gedeelde link";
  }, [share]);

  let page;
  if (problem?.kind === "password") {
    page = <PasswordForm message={problem.message} busy={busy} onSubmit={open} />;
  } else if (problem) {
    page = (
      <main>
        <h1>{problem.kind === "gone" ? problem.message : "Dat lukte niet"}</h1>
        {problem.kind === "gone" ? (
          <p className="muted">Vraag wie de link stuurde om een nieuwe.</p>
        ) : (
          <>
            <p role="alert">{problem.message}</p>
            <button type="button" disabled={busy} onClick={() => open()}>
              Opnieuw proberen
            </button>
          </>
        )}
      </main>
    );
  } else if (!share || !link || !queue) {
    page = <main className="center">Laden…</main>;
  } else {
    const expiry = expiryLabel(share.expires_at);
    page = (
      <main className="wide">
        <div className="public-head">
          <h1>{share.name}</h1>
        </div>
        <p className="muted">
          Gedeeld door {share.owner_name}
          {expiry && ` · de link ${expiry}`}
        </p>
        {share.permission === "upload" ? (
          <UploadView share={share} queue={queue} />
        ) : share.kind === "file" ? (
          <FileView link={link} node={share.root} />
        ) : (
          <FolderView link={link} share={share} />
        )}
      </main>
    );
  }

  return (
    <>
      <header className="topbar">
        <strong>Platform</strong>
      </header>
      {page}
    </>
  );
}

function PasswordForm({
  message,
  busy,
  onSubmit,
}: {
  message: string | null;
  busy: boolean;
  onSubmit: (password: string) => void;
}) {
  const [password, setPassword] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (password) onSubmit(password);
  }

  return (
    <main className="center">
      <form className="card public-card" onSubmit={submit}>
        <h1>Wachtwoord nodig</h1>
        <p className="muted">Voor deze link is een wachtwoord nodig. Je krijgt het van wie de link stuurde.</p>
        <label>
          Wachtwoord
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {message && <p role="alert">{message}</p>}
        <button type="submit" disabled={busy}>
          Openen
        </button>
      </form>
    </main>
  );
}

/** Previews via de link. Geen live gebeurtenissen hier: wat nog gemaakt wordt, vragen we
 * een tijdje elke paar seconden opnieuw. */
function usePublicPreviews(link: PublicLink, nodes: PublicNode[] | undefined): Record<string, Preview> {
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const files = useMemo(() => (nodes ?? []).filter((n) => n.kind === "file"), [nodes]);
  const versions = files.map((n) => `${n.id}:${n.etag}`).join(",");

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let rounds = 0;
    setPreviews({});
    const ask = async (ids: string[]) => {
      const pending: string[] = [];
      for (let i = 0; i < ids.length && !stopped; i += BATCH) {
        try {
          const got = await link.previews(ids.slice(i, i + BATCH));
          if (stopped) return;
          setPreviews((old) => ({ ...old, ...got }));
          for (const [id, p] of Object.entries(got)) if (p.state === "pending") pending.push(id);
        } catch {
          return; // geen previews: dan de iconen
        }
      }
      if (pending.length && !stopped && ++rounds < PREVIEW_ROUNDS) timer = setTimeout(() => ask(pending), 3000);
    };
    if (files.length) void ask(files.map((n) => n.id));
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // Op versions en niet op files: dezelfde lijst opnieuw geladen vraagt niets opnieuw
  }, [versions, link]);

  return previews;
}

async function download(link: PublicLink, node: PublicNode) {
  const a = document.createElement("a");
  a.href = await link.downloadUrl(node.id);
  a.download = node.name;
  a.click();
}

/** Eén gedeeld bestand: bekijken (afbeelding, video, audio, eerste pagina van een pdf) en downloaden. */
function FileView({ link, node }: { link: PublicLink; node: PublicNode }) {
  const nodes = useMemo(() => [node], [node]);
  const preview = usePublicPreviews(link, nodes)[node.id];
  const [error, setError] = useState<string | null>(null);
  const kind = category(node);
  const source = useCallback((inline: boolean) => link.downloadUrl(node.id, inline), [link, node.id]);

  async function openInTab() {
    try {
      window.open(await source(true), "_blank", "noopener");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="public-file">
      <div>
        <PreviewBody node={node} preview={preview} source={source} original />
      </div>
      <p className="muted" style={{ margin: 0 }}>
        {previewDetails(node, preview?.meta ?? {})}
      </p>
      <div className="toolbar" style={{ margin: 0 }}>
        <button
          type="button"
          className="primary"
          onClick={() => download(link, node).catch((e: Error) => setError(e.message))}
        >
          Downloaden
        </button>
        {(kind === "pdf" || kind === "image") && (
          <button type="button" onClick={openInTab}>
            Openen in nieuw tabblad
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

/** De map die open staat binnen een gedeelde map, in de adresbalk (?map=) voor terug en vooruit. */
function useFolderParam(): [string | null, (id: string | null) => void] {
  const read = () => new URLSearchParams(window.location.search).get("map");
  const [folder, setFolder] = useState(read);
  useEffect(() => {
    const update = () => setFolder(read());
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  const go = useCallback((id: string | null) => {
    const href = id ? `${window.location.pathname}?${new URLSearchParams({ map: id })}` : window.location.pathname;
    if (href !== window.location.pathname + window.location.search) window.history.pushState(null, "", href);
    setFolder(id);
  }, []);
  return [folder, go];
}

/** Een gedeelde map bekijken: submappen openen, bestanden bekijken en downloaden. */
function FolderView({ link, share }: { link: PublicLink; share: PublicShare }) {
  const [folder, go] = useFolderParam();
  const [listing, setListing] = useState<PublicListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<PublicNode | null>(null);
  const loads = useRef(0);
  const previews = usePublicPreviews(link, listing?.children);

  useEffect(() => {
    const seq = ++loads.current;
    link
      .children(folder && folder !== share.root.id ? folder : undefined)
      .then((result) => {
        if (seq !== loads.current) return;
        setListing(result);
        setError(null);
      })
      .catch((e: Error) => {
        if (seq !== loads.current) return;
        setListing(null);
        setError(e.message);
      });
  }, [link, folder, share.root.id]);

  const fail = (e: Error) => setError(e.message);

  return (
    <>
      {listing && listing.path.length > 1 && (
        <nav className="crumbs" aria-label="Pad">
          {listing.path.map((p, i) => (
            <span key={p.id}>
              {i > 0 && " / "}
              {i === listing.path.length - 1 ? (
                <span>{p.name}</span>
              ) : (
                <button type="button" className="link" onClick={() => go(i === 0 ? null : p.id)}>
                  {p.name}
                </button>
              )}
            </span>
          ))}
        </nav>
      )}
      {error && (
        <>
          <p role="alert">{error}</p>
          {folder && (
            <p>
              <button type="button" className="link" onClick={() => go(null)}>
                Terug naar {share.name}
              </button>
            </p>
          )}
        </>
      )}
      {listing && listing.children.length === 0 && <p className="muted">Deze map is leeg.</p>}
      {listing && listing.children.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Grootte</th>
                <th className="wide-only">Gewijzigd</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {listing.children.map((n) => (
                <tr key={n.id}>
                  <td>
                    <button type="button" className="link" onClick={() => (n.kind === "dir" ? go(n.id) : setShown(n))}>
                      <Thumb node={n} preview={previews[n.id]} />
                      {n.name}
                    </button>
                  </td>
                  <td>{n.kind === "file" ? formatBytes(n.size) : ""}</td>
                  <td className="wide-only">{formatDate(n.modified_at)}</td>
                  <td className="actions">
                    {n.kind === "file" && (
                      <button type="button" onClick={() => download(link, n).catch(fail)}>
                        Download
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {shown && (
        <PreviewPanel
          node={shown}
          preview={previews[shown.id]}
          source={(inline) => link.downloadUrl(shown.id, inline)}
          onClose={() => setShown(null)}
          onDownload={() => download(link, shown).catch(fail)}
        />
      )}
    </>
  );
}

/** Uploaden via een link: bestanden komen in de gedeelde map; wat er al in staat, zie je niet. */
function UploadView({ share, queue }: { share: PublicShare; queue: UploadQueue }) {
  const items = useSyncExternalStore(queue.subscribe, queue.getSnapshot);
  const [arrived, setArrived] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  // De sleutel voor "hetzelfde bestand verder uploaden" (localStorage); niet het token
  const parent = `public:${share.root.id}`;
  const running = items.some((u) => u.state === "running" || u.state === "waiting");

  useEffect(
    () =>
      queue.onFinished((_parent, ok, file) => {
        if (ok) setArrived((names) => [...names, file.name]);
      }),
    [queue],
  );

  // Weggaan tijdens het uploaden: de browser vraagt eerst of dat de bedoeling is
  useEffect(() => {
    if (!running) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [running]);

  function add(files: FileList | null) {
    if (!files?.length) return;
    setError(null);
    queue.add(parent, Array.from(files)).catch((e: Error) => setError(e.message));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    add(e.dataTransfer.files);
  }

  return (
    <>
      <p>Hier kun je bestanden voor {share.owner_name} neerzetten. Wat er al in de map staat, zie je niet.</p>
      <div
        className={`dropzone${dragging ? " dropping" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span aria-hidden="true" style={{ fontSize: "2rem" }}>
          ⬆️
        </span>
        <span>Sleep bestanden hierheen, of</span>
        <button type="button" className="primary" onClick={() => input.current?.click()}>
          Bestanden kiezen
        </button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
        <small className="muted">
          Valt de verbinding weg, kies dan hetzelfde bestand opnieuw: het gaat verder waar het was.
        </small>
      </div>
      {error && <p role="alert">{error}</p>}
      {items.length > 0 && (
        <ul className="uploads">
          {items.map((u) => (
            <li key={u.key}>
              <span className="upload-name">{u.file.name}</span>
              {u.state === "failed" ? (
                <>
                  <span role="alert">{u.error}</span>
                  <button type="button" onClick={() => queue.run(u.key)}>
                    Opnieuw proberen
                  </button>
                  <button type="button" onClick={() => queue.discard(u.key)}>
                    Weggooien
                  </button>
                </>
              ) : u.state === "discarding" ? (
                <span className="muted">Weggooien…</span>
              ) : (
                <>
                  <progress max={1} value={u.progress} />
                  <span className="muted">{Math.floor(u.progress * 100)} %</span>
                  {u.state === "running" && (
                    <button type="button" onClick={() => queue.cancel(u.key)}>
                      Annuleren
                    </button>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {arrived.length > 0 && (
        <div className="arrived" role="status">
          <strong>{items.length === 0 ? "Je bestanden zijn aangekomen" : "Al aangekomen"}</strong>
          <ul>
            {arrived.map((name, i) => (
              <li key={i}>{name}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
