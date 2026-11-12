// Tekst en code: de eerste 512 KB (een Range-verzoek), daarna "Meer laden". De codering raden we
// (decode.ts) en is te kiezen; code krijgt kleuren (highlight.ts, boven 100 KB in een worker),
// markdown en CSV kunnen ook met opmaak. Alleen de regels in beeld staan in de DOM. Nooit
// innerHTML: alles is tekst of een React-element.

import { type JSX, type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { Spinner } from "../ui/Spinner";
import { fixedRange } from "../ui/virtual";
import type { ViewerDetail } from "./api";
import { detectDelimiter, parseCsv } from "./csv";
import { decodeChunks, detectEncoding, ENCODINGS, type Encoding, isEncoding } from "./decode";
import { type Highlighted, highlight, languageOf, lineTokens, MAX_HIGHLIGHT, plain, WORKER_FROM } from "./highlight";
import { markdown } from "./markdown";
import type { Source } from "./source";
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

export default function TextView({ item, detail, source }: { item: ViewerItem; detail: ViewerDetail; source: Source }): JSX.Element {
  const [chunks, setChunks] = useState<Uint8Array[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Encoding | null>(null);
  const modes = modesFor(item);
  const [mode, setMode] = useState<Mode>(modes[0]);
  const loadedBytes = chunks.reduce((n, c) => n + c.length, 0);
  const complete = !busy && (total !== null ? loadedBytes >= total : chunks.length > 0 && chunks[chunks.length - 1].length < CHUNK);

  async function load(start: number) {
    setBusy(true);
    setError(null);
    try {
      let got;
      try {
        got = await source.range(item, start, start + CHUNK);
      } catch {
        source.forget(item); // misschien een verlopen adres: één keer met een nieuw
        got = await source.range(item, start, start + CHUNK);
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

  return (
    <div className="text-view">
      <div className="text-tools" role="toolbar" aria-label={t("viewer.text.tools")}>
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
