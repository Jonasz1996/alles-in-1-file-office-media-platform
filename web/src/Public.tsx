// De pagina achter een publieke link (/s/<token>). Werkt zonder in te loggen: deze pagina
// gebruikt alleen de publieke API (publicApi.ts), nooit de login van de app. Bestanden openen in
// de viewer (met publicSource), met ?bestand= in het adres; de map die open staat in ?map=.

import {
  type DragEvent,
  type FormEvent,
  memo,
  type RefObject,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { formatBytes, formatDate, type Preview, type PublicNode, type PublicShare } from "./api";
import { errorText, t } from "./i18n";
import { markReady } from "./perf";
import { PublicAlbum } from "./photos";
import { type LinkProblem, linkProblem, linkToken, type PublicFolder, PublicLink } from "./publicApi";
import { navigate, type RouteProps, usePath, useQueryParam } from "./router";
import { expiryLabel } from "./sharing";
import { Thumb } from "./Thumb";
import { Button } from "./ui/Button";
import { ShortcutHelp } from "./ui/ShortcutHelp";
import { UploadQueue } from "./uploadQueue";
import { useViewerParam, VIEWER_PARAM, Viewer } from "./viewer";
import { formatDuration } from "./viewer/media";
import { publicSource } from "./viewer/source";
import "./files.css";
import "./public.css";

const BATCH = 500;
/** Zo vaak (elke 3 s) vragen we een preview die nog gemaakt wordt opnieuw. */
const PREVIEW_ROUNDS = 20;
const FOLDER_PARAM = "map";
const NO_NODES: PublicNode[] = [];

/** De route /s/:token (buiten de login). */
export default function PublicPage(_: RouteProps) {
  // Strenger dan het routepatroon: een token met vreemde tekens is meteen "bestaat niet"
  const token = linkToken(usePath());
  return <Public key={token ?? ""} token={token} />;
}

export function Public({ token }: { token: string | null }) {
  const link = useMemo(() => (token ? new PublicLink(token) : null), [token]);
  // Uploads lopen door als de pagina even om het wachtwoord vraagt; daarna kun je verder
  const queue = useMemo(() => (link ? new UploadQueue(link, { send: link.send }) : null), [link]);
  const [share, setShare] = useState<PublicShare | null>(() => link?.share ?? null);
  const [problem, setProblem] = useState<LinkProblem | null>(() => (token ? null : { kind: "gone", message: null }));
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
    document.title = share ? t("public.pageTitle", { name: share.name }) : t("public.title");
  }, [share]);

  // Iets te zien dat niet meer wacht op de server: een fout of het wachtwoordformulier
  useEffect(() => {
    if (problem) markReady("public");
  }, [problem]);

  let page;
  if (problem?.kind === "password") {
    page = <PasswordForm message={problem.message} busy={busy} onSubmit={open} />;
  } else if (problem) {
    const gone = problem.kind === "gone";
    const heading = gone
      ? (problem.message ?? (problem.expired ? t("public.expired") : t("public.gone")))
      : t("public.failed");
    page = (
      <main>
        <h1>{heading}</h1>
        {gone ? (
          <p className="muted">{t("public.askNew")}</p>
        ) : (
          <>
            <p role="alert">{problem.message}</p>
            <Button disabled={busy} onClick={() => open()}>
              {t("common.retry")}
            </Button>
          </>
        )}
      </main>
    );
  } else if (!share || !link || !queue) {
    page = <main className="center">{t("common.loading")}</main>;
  } else {
    const expiry = expiryLabel(share.expires_at);
    page = (
      <main className="wide">
        <div className="public-head">
          <h1>{share.name}</h1>
        </div>
        <p className="muted">
          {t("public.sharedBy", { owner: share.owner_name })}
          {expiry && t("public.linkExpiry", { expiry })}
        </p>
        {share.kind === "album" ? (
          <Suspense fallback={<p className="muted">{t("common.loading")}</p>}>
            <PublicAlbum link={link} share={share} />
          </Suspense>
        ) : share.permission === "upload" ? (
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
        <strong>{t("public.brand")}</strong>
      </header>
      {page}
      <ShortcutHelp />
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
        <h1>{t("public.password.title")}</h1>
        <p className="muted">{t("public.password.intro")}</p>
        <label>
          {t("public.password.label")}
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
        <Button type="submit" variant="primary" disabled={busy}>
          {t("public.password.open")}
        </Button>
      </form>
    </main>
  );
}

/** Previews via de link. Geen live gebeurtenissen hier: wat nog gemaakt wordt, vragen we
 * een tijdje elke paar seconden opnieuw. initial: wat er al is (uit GET /public/listing).
 * upTo: alleen voor de bestanden onder de eerste upTo items (wat in beeld is of bijna); wat
 * daarna komt, vragen we pas als upTo groeit. */
function usePublicPreviews(
  link: PublicLink,
  nodes: readonly PublicNode[] | undefined,
  initial?: Record<string, Preview>,
  upTo = Infinity,
): Record<string, Preview> {
  const all = nodes ?? NO_NODES;
  const versions = useMemo(
    () =>
      all
        .filter((n) => n.kind === "file")
        .map((n) => `${n.id}:${n.etag}`)
        .join(","),
    [all],
  );
  // Wat er na initial bij kwam, voor deze bestanden (versions)
  const [later, setLater] = useState<{ versions: string; got: Record<string, Preview> }>({ versions: "", got: {} });
  const got = later.versions === versions ? later.got : null;
  const record = useMemo(() => (got ? { ...initial, ...got } : (initial ?? {})), [initial, got]);
  const current = useRef({ versions, record });
  const asked = useRef({ versions: "", ids: new Set<string>() });

  useEffect(() => {
    current.current = { versions, record };
  });

  /** Een antwoord erbij, als het nog over deze bestanden gaat. */
  const merge = useCallback((forVersions: string, answer: Record<string, Preview>) => {
    if (current.current.versions !== forVersions) return;
    setLater((old) => ({
      versions: forVersions,
      got: { ...(old.versions === forVersions ? old.got : {}), ...answer },
    }));
  }, []);

  // Wat in (of bij) beeld komt en nog geen preview heeft
  const wanted = useMemo(
    () =>
      all
        .slice(0, upTo)
        .filter((n) => n.kind === "file" && !initial?.[n.id])
        .map((n) => n.id),
    [all, upTo, initial],
  );
  useEffect(() => {
    if (asked.current.versions !== versions) asked.current = { versions, ids: new Set() };
    const ids = wanted.filter((id) => !asked.current.ids.has(id));
    if (!ids.length) return;
    for (const id of ids) asked.current.ids.add(id);
    void (async () => {
      for (let i = 0; i < ids.length; i += BATCH) {
        try {
          merge(versions, await link.previews(ids.slice(i, i + BATCH)));
        } catch {
          return; // geen previews: dan de iconen
        }
      }
    })();
  }, [wanted, versions, link, merge]);

  // Wat nog gemaakt wordt: een tijdje (PREVIEW_ROUNDS keer) elke 3 s opnieuw vragen
  useEffect(() => {
    let rounds = 0;
    const timer = setInterval(() => {
      if (++rounds >= PREVIEW_ROUNDS) clearInterval(timer);
      const pending = Object.entries(current.current.record)
        .filter(([, p]) => p.state === "pending")
        .map(([id]) => id);
      if (pending.length) link.previews(pending.slice(0, BATCH)).then((answer) => merge(versions, answer), () => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [versions, link, merge]);

  return record;
}

/** Tot welk item (exclusief) de rijen in tbody in of bij beeld zijn geweest (een half scherm
 * extra), zodat previews pas gevraagd worden voor wat je kunt zien. Opnieuw bij een nieuwe
 * lijst (rows); rijen hebben data-index. */
function useNearRows(body: RefObject<HTMLElement | null>, rows: readonly unknown[], shown: number): number {
  const [near, setNear] = useState<{ rows: readonly unknown[]; upTo: number }>({ rows, upTo: 0 });
  const observer = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") {
      setNear({ rows, upTo: Infinity });
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        let max = -1;
        for (const e of entries) {
          if (e.isIntersecting) max = Math.max(max, Number((e.target as HTMLElement).dataset.index));
        }
        if (max < 0) return;
        setNear((old) => {
          const upTo = old.rows === rows ? old.upTo : 0;
          return max + 1 > upTo ? { rows, upTo: max + 1 } : old;
        });
      },
      { rootMargin: "50% 0px" },
    );
    observer.current = io;
    return () => {
      io.disconnect();
      observer.current = null;
    };
  }, [rows]);

  // Nieuwe rijen ook bekijken (twee keer observe op dezelfde rij doet niets)
  useEffect(() => {
    const io = observer.current;
    if (io && body.current) for (const tr of body.current.querySelectorAll("tr[data-index]")) io.observe(tr);
  }, [body, rows, shown]);

  return near.rows === rows ? near.upTo : 0;
}

/** Zoveel rijen meteen (een scherm vol, ook op een groot scherm); de rest per ROWS_STEP in een
 * eigen taak, zodat een grote map geen lange taak geeft en de bovenkant er meteen staat. */
const ROWS_FIRST = 24;
const ROWS_STEP = 24;

/** Zoals .wide-only in base.css: op een smal scherm zijn grootte en datum er niet. */
const WIDE = "(min-width: 40.0625rem)";

function subscribeWide(onChange: () => void): () => void {
  const query = window.matchMedia?.(WIDE);
  query?.addEventListener?.("change", onChange);
  return () => query?.removeEventListener?.("change", onChange);
}

/** Of de brede kolommen te zien zijn; zo niet, dan maken we hun tekst ook niet (de datum
 * opmaken kost op een trage telefoon per lijst een lange taak). */
function useWide(): boolean {
  return useSyncExternalStore(
    subscribeWide,
    () => window.matchMedia?.(WIDE).matches ?? true,
    () => true,
  );
}

/** Hoeveel rijen van rows er nu staan: eerst ROWS_FIRST, dan elke taak ROWS_STEP meer. */
function useGrowingRows(rows: readonly unknown[]): number {
  const [grown, setGrown] = useState<{ rows: readonly unknown[]; count: number }>({ rows, count: ROWS_FIRST });
  const count = Math.min(rows.length, grown.rows === rows ? grown.count : ROWS_FIRST);
  useEffect(() => {
    if (count >= rows.length) return;
    const timer = setTimeout(() => setGrown({ rows, count: count + ROWS_STEP }), 0);
    return () => clearTimeout(timer);
  }, [rows, count]);
  return count;
}

async function download(link: PublicLink, node: PublicNode) {
  const a = document.createElement("a");
  a.href = await link.downloadUrl(node.id);
  a.download = node.name;
  a.click();
}

/** "1920 × 1080 · 2,1 MB · gewijzigd …" onder een gedeeld bestand. */
function fileDetails(node: PublicNode, preview: Preview | undefined): string {
  const meta = preview?.meta ?? {};
  return [
    meta.width && meta.height ? t("public.file.pixels", { w: meta.width, h: meta.height }) : null,
    meta.duration ? formatDuration(meta.duration) : null,
    meta.pages ? t("public.file.pages", { n: meta.pages }) : null,
    formatBytes(node.size),
    t("public.file.modified", { date: formatDate(node.modified_at) }),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** De viewer over deze bestanden, op ?bestand=. */
function PublicViewer({ link, files }: { link: PublicLink; files: PublicNode[] }) {
  const [viewing, setViewing] = useViewerParam();
  const source = useMemo(() => publicSource(link), [link]);
  const index = viewing ? files.findIndex((f) => f.id === viewing) : -1;
  if (index < 0) return null;
  return (
    <Suspense fallback={null}>
      <Viewer
        items={files}
        index={index}
        onIndexChange={(i) => setViewing(files[i].id)}
        onClose={() => setViewing(null)}
        source={source}
      />
    </Suspense>
  );
}

/** Eén gedeeld bestand: de preview, bekijken in de viewer en downloaden. */
function FileView({ link, node }: { link: PublicLink; node: PublicNode }) {
  const nodes = useMemo(() => [node], [node]);
  const preview = usePublicPreviews(link, nodes)[node.id];
  const [, setViewing] = useViewerParam();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => markReady("public"), []);

  return (
    <div className="public-file">
      <button type="button" className="public-preview" onClick={() => setViewing(node.id)}>
        {preview?.large ? <img src={preview.large} alt="" /> : <Thumb node={node} preview={preview} />}
        <span className="sr-only">{t("public.file.view")}</span>
      </button>
      <p className="muted details">{fileDetails(node, preview)}</p>
      <div className="toolbar">
        <Button variant="primary" icon="image" onClick={() => setViewing(node.id)}>
          {t("public.file.view")}
        </Button>
        <Button icon="download" onClick={() => download(link, node).catch((e: unknown) => setError(errorText(e)))}>
          {t("public.file.download")}
        </Button>
      </div>
      {error && <p role="alert">{error}</p>}
      <PublicViewer link={link} files={nodes} />
    </div>
  );
}

/** Eén rij in een gedeelde map; memo: een nieuwe preview of meer rijen tekent de andere
 * rijen niet opnieuw. */
const FolderRow = memo(function FolderRow({
  node,
  index,
  preview,
  wide,
  onOpen,
  onDownload,
}: {
  node: PublicNode;
  index: number;
  preview: Preview | undefined;
  wide: boolean;
  onOpen(node: PublicNode): void;
  onDownload(node: PublicNode): void;
}) {
  return (
    <tr role="row" data-index={index}>
      <td role="cell">
        <button type="button" className="link" onClick={() => onOpen(node)}>
          <Thumb node={node} preview={preview} />
          <span className="name">{node.name}</span>
        </button>
      </td>
      <td role="cell" className="wide-only">
        {wide && node.kind === "file" ? formatBytes(node.size) : ""}
      </td>
      <td role="cell" className="wide-only">
        {wide ? formatDate(node.modified_at) : ""}
      </td>
      <td role="cell" className="actions">
        {node.kind === "file" && (
          <button
            type="button"
            aria-label={t("public.folder.downloadName", { name: node.name })}
            onClick={() => onDownload(node)}
          >
            {t("public.folder.download")}
          </button>
        )}
      </td>
    </tr>
  );
});

/** Een gedeelde map bekijken: submappen openen, bestanden bekijken en downloaden. De lijst
 * komt met zijn previews in één verzoek (GET /public/listing). */
function FolderView({ link, share }: { link: PublicLink; share: PublicShare }) {
  const [folder] = useQueryParam(FOLDER_PARAM);
  const [listing, setListing] = useState<PublicFolder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setViewing] = useViewerParam();
  const loads = useRef(0);
  const children = listing?.children ?? NO_NODES;
  const files = useMemo(() => children.filter((n) => n.kind === "file"), [children]);
  const shown = useGrowingRows(children);
  const body = useRef<HTMLTableSectionElement>(null);
  const near = useNearRows(body, children, shown);
  const previews = usePublicPreviews(link, listing?.children, listing?.previews, near);
  const wide = useWide();

  /** Naar een andere map (een nieuwe stap in de geschiedenis); de viewer gaat dan dicht. */
  const go = useCallback((id: string | null) => {
    const params = new URLSearchParams(window.location.search);
    params.delete(VIEWER_PARAM);
    if (id) params.set(FOLDER_PARAM, id);
    else params.delete(FOLDER_PARAM);
    const qs = params.toString();
    navigate(`${window.location.pathname}${qs ? `?${qs}` : ""}`);
  }, []);

  useEffect(() => {
    const seq = ++loads.current;
    link
      .listing(folder && folder !== share.root.id ? folder : undefined, ROWS_FIRST)
      .then((result) => {
        if (seq !== loads.current) return;
        setListing(result);
        setError(null);
      })
      .catch((e: unknown) => {
        if (seq !== loads.current) return;
        setListing(null);
        setError(errorText(e));
      });
  }, [link, folder, share.root.id]);

  useEffect(() => {
    if (listing || error) markReady("public");
  }, [listing, error]);

  const fail = useCallback((e: unknown) => setError(errorText(e)), []);
  const onOpen = useCallback((n: PublicNode) => (n.kind === "dir" ? go(n.id) : setViewing(n.id)), [go, setViewing]);
  const onDownload = useCallback((n: PublicNode) => void download(link, n).catch(fail), [link, fail]);

  return (
    <>
      {listing && listing.path.length > 1 && (
        <nav className="crumbs" aria-label={t("public.folder.crumbs")}>
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
                {t("public.folder.backTo", { name: share.name })}
              </button>
            </p>
          )}
        </>
      )}
      {listing && children.length === 0 && <p className="muted">{t("public.folder.empty")}</p>}
      {listing && children.length > 0 && (
        <div className="table-wrap">
          {/* De rollen staan er expliciet bij: public.css legt de rijen als grid neer (zodat
              rijen buiten beeld met content-visibility niet getekend worden), en dan houdt de
              browser de tabelrollen niet altijd vast */}
          <table className="public-list" role="table">
            <thead role="rowgroup">
              <tr role="row">
                <th role="columnheader">{t("public.folder.name")}</th>
                <th role="columnheader" className="wide-only">
                  {t("public.folder.size")}
                </th>
                <th role="columnheader" className="wide-only">
                  {t("public.folder.modified")}
                </th>
                <th role="columnheader">
                  <span className="sr-only">{t("public.folder.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup" ref={body}>
              {children.slice(0, shown).map((n, i) => (
                <FolderRow
                  key={n.id}
                  node={n}
                  index={i}
                  preview={previews[n.id]}
                  wide={wide}
                  onOpen={onOpen}
                  onDownload={onDownload}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <PublicViewer link={link} files={files} />
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

  useEffect(() => markReady("public"), []);

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
    queue.add(parent, Array.from(files)).catch((e: unknown) => setError(errorText(e)));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    add(e.dataTransfer.files);
  }

  return (
    <>
      <p>{t("public.upload.intro", { owner: share.owner_name })}</p>
      <div
        className={`dropzone${dragging ? " dropping" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span aria-hidden="true" className="dropzone-icon">
          ⬆️
        </span>
        <span>{t("public.upload.drop")}</span>
        <Button variant="primary" onClick={() => input.current?.click()}>
          {t("public.upload.choose")}
        </Button>
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
        <small className="muted">{t("public.upload.resume")}</small>
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
                    {t("public.upload.retry")}
                  </button>
                  <button type="button" onClick={() => queue.discard(u.key)}>
                    {t("public.upload.discard")}
                  </button>
                </>
              ) : u.state === "discarding" ? (
                <span className="muted">{t("public.upload.discarding")}</span>
              ) : (
                <>
                  <progress max={1} value={u.progress} />
                  <span className="muted">{t("public.upload.percent", { n: Math.floor(u.progress * 100) })}</span>
                  {u.state === "running" && (
                    <button type="button" onClick={() => queue.cancel(u.key)}>
                      {t("public.upload.cancel")}
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
          <strong>{items.length === 0 ? t("public.upload.arrived") : t("public.upload.arrivedSoFar")}</strong>
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
