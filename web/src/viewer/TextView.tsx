// Tekst en code: de eerste 512 KB (een Range-verzoek), daarna "Meer laden". De codering raden we
// (decode.ts) en is te kiezen; code krijgt kleuren (highlight.ts, boven 100 KB in een worker),
// markdown en CSV kunnen ook met opmaak. Alleen de regels in beeld staan in de DOM. Nooit
// innerHTML: alles is tekst of een React-element.
//
// Bewerken (0.8.3): de knop "Bewerken" (ingelogd, schrijfrecht, geen eerdere versie, hooguit 5 MB;
// textEdit.ts) laadt de hele tekst in een tekstveld. Opslaan (de knop of Ctrl+S) maakt van de
// vorige inhoud een versie; een BOM en CRLF blijven. Een nieuw tekstbestand (Nieuw → Tekstbestand)
// opent meteen zo (startEditing, van de prop editText van de viewer). Wijzigingen die nog niet zijn
// opgeslagen: de viewer vraagt eerst (leaveGuard.ts).
//
// Een nieuwe etag (iemand sloeg op; de viewer geeft TextView dan hetzelfde key, viewKeys.ts):
// tijdens het lezen de nieuwe inhoud laden; tijdens het bewerken blijft de tekst, met de melding
// "buiten de editor veranderd" en de keuze "Opnieuw laden" of "Toch opslaan". Opslaan stuurt de
// etag mee (If-Match): een ander overschrijven gaat nooit zonder dat je het ziet.

import {
  type JSX,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { api } from "../api";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { Spinner } from "../ui/Spinner";
import { announce } from "../ui/toast";
import { fixedRange } from "../ui/virtual";
import type { ViewerDetail } from "./api";
import { detectDelimiter, parseCsv } from "./csv";
import { decodeChunks, detectEncoding, ENCODINGS, type Encoding, isEncoding } from "./decode";
import { type Highlighted, highlight, languageOf, lineTokens, MAX_HIGHLIGHT, plain, WORKER_FROM } from "./highlight";
import { setLeaveGuard } from "./leaveGuard";
import { markdown } from "./markdown";
import type { Source } from "./source";
import { canEditText, isSaveKey, MAX_EDIT_BYTES, putContent, type TextTarget, TextEditSession } from "./textEdit";
import type { ViewerItem } from "./types";

export const CHUNK = 512 * 1024;
const LINE_H = 20;
/** Zo veel tekens van één regel tonen (een geminificeerd bestand is één lange regel) */
const LINE_CHARS = 20_000;

type Mode = "code" | "markdown" | "table";

function modesFor(item: ViewerItem): Mode[] {
  const name = item.name.toLowerCase();
  const mime = (item.mime ?? "").toLowerCase();
  if (/\.(md|markdown)$/.test(name) || mime === "text/markdown") return ["markdown", "code"];
  if (/\.(csv|tsv)$/.test(name) || mime === "text/csv" || mime === "text/tab-separated-values") return ["table", "code"];
  return ["code"];
}

/** Kleuren voor deze tekst: klein meteen, groot in een worker, heel groot niet. */
function useLines(text: string, language: string | null): Highlighted {
  const small = text.length < WORKER_FROM;
  const now = useMemo(() => (small && language ? highlight(text, language) : plain(text)), [text, language, small]);
  const [worked, setWorked] = useState<Highlighted | null>(null);

  useEffect(() => {
    if (small || !language || text.length > MAX_HIGHLIGHT || typeof Worker === "undefined") return;
    const worker = new Worker(new URL("./highlight.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<{ result: Omit<Highlighted, "text"> }>) => {
      setWorked({ ...e.data.result, text });
      worker.terminate();
    };
    worker.onerror = () => worker.terminate(); // dan blijft het gewone tekst
    worker.postMessage({ id: 1, text, language });
    return () => worker.terminate();
  }, [text, language, small]);

  return worked && worked.text === text && !small ? worked : now;
}

export default function TextView({
  item,
  detail,
  source,
  startEditing = false,
}: {
  item: ViewerItem;
  detail: ViewerDetail;
  source: Source;
  /** Meteen bewerken (een nieuw tekstbestand) */
  startEditing?: boolean;
}): JSX.Element {
  const [chunks, setChunks] = useState<Uint8Array[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Encoding | null>(null);
  const modes = modesFor(item);
  const [mode, setMode] = useState<Mode>(modes[0]);
  const loadedBytes = chunks.reduce((n, c) => n + c.length, 0);
  const complete = !busy && (total !== null ? loadedBytes >= total : chunks.length > 0 && chunks[chunks.length - 1].length < CHUNK);
  const editable = canEditText(item as ViewerItem & TextTarget, source.kind);
  // Het item van nu (de etag verandert terwijl TextView blijft)
  const current = useRef(item);
  current.current = item;
  const session = useMemo(
    () =>
      new TextEditSession({
        async load() {
          const it = current.current;
          try {
            return (await source.range(it, 0, MAX_EDIT_BYTES + 1)).bytes;
          } catch {
            source.forget(it); // misschien een verlopen adres: één keer met een nieuw
            return (await source.range(it, 0, MAX_EDIT_BYTES + 1)).bytes;
          }
        },
        save: (body, ifMatch) => putContent(api, current.current.id, body, ifMatch),
        errorText,
      }),
    [],
  );
  const edit = useSyncExternalStore(session.subscribe, session.getState);
  const editing = edit.phase === "editing" || edit.phase === "saving";
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => setLeaveGuard(() => session.dirty()), [session]);

  // De etag van de inhoud die het lezen toont
  const shown = useRef(item.etag);
  useEffect(() => {
    session.seen(item.etag);
    if (item.etag === shown.current) return;
    if (session.getState().phase !== "reading") return; // bewerken: de melding, de tekst blijft
    // Lezen: de nieuwe inhoud
    shown.current = item.etag;
    source.forget(item);
    setChunks([]);
    setTotal(null);
    void load(0);
  }, [item.etag]);

  async function startEdit() {
    if (await session.start(current.current.etag)) requestAnimationFrame(() => field.current?.focus());
  }

  async function save(force = false) {
    const done = await (force ? session.overwrite() : session.save());
    if (!done) return;
    // Wat er nu staat, is wat de viewer toont als je stopt met bewerken
    shown.current = done.node.etag;
    source.forget(item);
    setChunks([done.bytes]);
    setTotal(done.bytes.length);
    setChosen(null);
    announce(t("viewer.text.edit.saved", { name: done.node.name }));
  }

  /** De inhoud van nu laden; de eigen wijzigingen weg (eerst de vraag). */
  async function reloadEdit() {
    if (session.dirty() && !(await askDiscard())) return;
    shown.current = current.current.etag;
    source.forget(current.current);
    if (await session.reload(current.current.etag)) requestAnimationFrame(() => field.current?.focus());
  }

  function askDiscard(): Promise<boolean> {
    return confirm({
      title: t("viewer.text.edit.leaveTitle"),
      message: t("viewer.text.edit.leaveMessage"),
      confirm: t("viewer.text.edit.leaveConfirm"),
      danger: true,
    });
  }

  async function stopEdit() {
    if (session.dirty() && !(await askDiscard())) return;
    session.stop();
    // Intussen opgeslagen door een ander: lezen toont de inhoud van nu
    if (current.current.etag !== shown.current) {
      shown.current = current.current.etag;
      source.forget(current.current);
      setChunks([]);
      setTotal(null);
      void load(0);
    }
  }

  useEffect(() => {
    // Een nieuw tekstbestand (Nieuw → Tekstbestand): meteen bewerken
    if (editable && startEditing) void startEdit();
  }, []);

  async function load(start: number) {
    setBusy(true);
    setError(null);
    const it = current.current;
    try {
      let got;
      try {
        got = await source.range(it, start, start + CHUNK);
      } catch {
        source.forget(it); // misschien een verlopen adres: één keer met een nieuw
        got = await source.range(it, start, start + CHUNK);
      }
      const { bytes, total: size } = got;
      setChunks((old) => [...old, bytes]);
      setTotal(size ?? (bytes.length < CHUNK ? start + bytes.length : null));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load(0);
    // Eén keer per bestand (de viewer geeft elk bestand een eigen key)
  }, []);

  const guessed = useMemo<Encoding>(() => {
    if (chunks.length === 0) return "utf-8";
    const server = detail.meta.encoding;
    // De server las alleen de eerste regels: zijn keuze geldt, tenzij de bytes het tegenspreken
    const own = detectEncoding(chunks[0], complete && chunks.length === 1);
    return isEncoding(server) && (server !== "utf-8" || own === "utf-8") ? server : own;
  }, [chunks.length > 0, detail.meta.encoding]);
  const encoding = chosen ?? guessed;
  const text = useMemo(() => decodeChunks(chunks, encoding, complete), [chunks, encoding, complete]);
  const language = mode === "code" ? languageOf(item.name) : null;
  const lines = useLines(text, language);

  if (editing) {
    const dirty = edit.text !== edit.saved;
    return (
      <div className="text-view">
        <div className="text-tools" role="toolbar" aria-label={t("viewer.text.tools")}>
          <Button
            variant="primary"
            busy={edit.phase === "saving"}
            disabled={!dirty || edit.changedOutside}
            aria-keyshortcuts="Control+S"
            onClick={() => void save()}
          >
            {t("viewer.text.edit.save")}
          </Button>
          <Button onClick={() => void stopEdit()}>{t("viewer.text.edit.done")}</Button>
          <span className="muted text-size" role="status">
            {dirty ? t("viewer.text.edit.unsaved") : t("viewer.text.edit.clean")}
          </span>
        </div>
        <textarea
          ref={field}
          className="text-edit"
          value={edit.text}
          spellCheck={false}
          aria-label={t("viewer.text.edit.field", { name: item.name })}
          onChange={(e) => session.change(e.target.value)}
          onKeyDown={(e) => {
            if (!isSaveKey(e)) return;
            e.preventDefault();
            e.stopPropagation();
            void save();
          }}
        />
        {edit.changedOutside ? (
          <div className="text-changed" role="alert">
            <p>{t("viewer.text.edit.changedOutside")}</p>
            <Button disabled={edit.phase === "saving"} onClick={() => void reloadEdit()}>
              {t("viewer.text.edit.reload")}
            </Button>
            <Button variant="danger" busy={edit.phase === "saving"} onClick={() => void save(true)}>
              {t("viewer.text.edit.overwrite")}
            </Button>
          </div>
        ) : (
          edit.error && <p role="alert">{edit.error}</p>
        )}
      </div>
    );
  }

  if (chunks.length === 0) {
    return error ? (
      <p className="viewer-message" role="alert">
        {error}
      </p>
    ) : (
      <div className="viewer-message">
        <Spinner label={t("viewer.loading")} />
      </div>
    );
  }

  let body: ReactNode;
  if (mode === "markdown") body = <div className="viewer-scroll md">{markdown(text)}</div>;
  else if (mode === "table") body = <CsvTable text={text} name={item.name} />;
  else body = <Lines lines={lines} />;

  const editProblem =
    edit.problem === "encoding" ? t("viewer.text.edit.encoding") : edit.problem === "too_large" ? t("viewer.text.edit.tooLarge") : null;

  return (
    <div className="text-view">
      <div className="text-tools" role="toolbar" aria-label={t("viewer.text.tools")}>
        {editable && (
          <Button icon="rename" busy={edit.phase === "loading"} onClick={() => void startEdit()}>
            {t("viewer.text.edit.start")}
          </Button>
        )}
        {modes.length > 1 &&
          modes.map((m) => (
            <button key={m} type="button" className="btn ghost" aria-pressed={mode === m} onClick={() => setMode(m)}>
              {t(`viewer.text.mode.${m}`)}
            </button>
          ))}
        <label className="text-encoding">
          <span>{t("viewer.text.encoding")}</span>
          <select value={encoding} onChange={(e) => setChosen(e.target.value as Encoding)}>
            {ENCODINGS.map((enc) => (
              <option key={enc} value={enc}>
                {t(`viewer.text.encodings.${enc}`)}
              </option>
            ))}
          </select>
        </label>
        <span className="muted text-size">
          {complete
            ? t("viewer.text.lines", { n: lines.lineCount })
            : t("viewer.text.partial", { n: Math.round(loadedBytes / 1024) })}
        </span>
      </div>
      {body}
      {!complete && (
        <div className="text-more">
          <Button busy={busy} onClick={() => void load(loadedBytes)}>
            {t("viewer.text.more")}
          </Button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {(editProblem || edit.error) && <p role="alert">{editProblem ?? edit.error}</p>}
    </div>
  );
}

/** De regels, alleen wat in beeld is (vaste hoogte per regel). */
function Lines({ lines }: { lines: Highlighted }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ top: 0, h: 600 });

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => setView((v) => ({ ...v, h: el.clientHeight }));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const count = lines.lineCount;
  const { start, end } = fixedRange(view.top, view.h, LINE_H, count, 20);
  const digits = String(count).length;
  const shown: JSX.Element[] = [];
  for (let i = start; i < end; i++) {
    shown.push(
      <div key={i} className="text-line">
        <span className="ln" aria-hidden="true">
          {i + 1}
        </span>
        <code>
          {lineTokens(lines, i, LINE_CHARS).map(([type, piece], j) =>
            type === "plain" ? piece : (
              <span key={j} className={`tok-${type}`}>
                {piece}
              </span>
            ),
          )}
        </code>
      </div>,
    );
  }
  return (
    <div
      ref={scroller}
      className="viewer-scroll text-scroll"
      tabIndex={0}
      role="region"
      aria-label={t("viewer.text.region")}
      onScroll={(e) => {
        const top = e.currentTarget.scrollTop;
        setView((v) => (v.top === top ? v : { ...v, top }));
      }}
    >
      <div className={`text-lines digits-${Math.min(digits, 7)}`} style={{ height: `${count * LINE_H}px` }}>
        <div className="text-window" style={{ transform: `translateY(${start * LINE_H}px)` }}>
          {shown}
        </div>
      </div>
    </div>
  );
}

/** CSV of TSV als tabel (de eerste rij als kop). */
function CsvTable({ text, name }: { text: string; name: string }) {
  const table = useMemo(() => parseCsv(text, detectDelimiter(text, name)), [text, name]);
  const [head, ...rows] = table.rows;
  if (!head) return <p className="viewer-message muted">{t("viewer.text.empty")}</p>;
  return (
    <div className="viewer-scroll">
      <div className="table-wrap" tabIndex={0} role="region" aria-label={t("viewer.text.region")}>
        <table className="csv">
          <thead>
            <tr>
              {head.map((c, i) => (
                <th key={i}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => (
                  <td key={j}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.truncated && <p className="muted">{t("viewer.text.tableTruncated")}</p>}
    </div>
  );
}
