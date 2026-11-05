// Een kantoordocument (docx, xlsx, pptx en de ODF-varianten) zoals de server het las
// (app/office/reader.py): de tekst met koppen, lijsten en tabellen, de bladen van een rekenblad
// of de dia's van een presentatie. Vereenvoudigd: zonder opmaak, afbeeldingen of formules.
//
// Fase 4 (O3): heeft de editor er een PDF van gemaakt, dan de echte pagina's (PdfView, met het
// label "Voorbeeld"). Wordt hij gemaakt, dan de eenvoudige weergave met een melding, en de
// viewer kijkt af en toe opnieuw. Gaat een bestand niet vanzelf naar de editor (pdf_state ask),
// dan een knop die het vraagt; nooit via een publieke link.

import { Fragment, type JSX, type ReactNode, useEffect, useRef, useState } from "react";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { Spinner } from "../ui/Spinner";
import { Tabs, tabPanelProps } from "../ui/Tabs";
import type { DocumentInfo, OfficeBlock, OfficeDoc, Run, ViewerDetail } from "./api";
import { convertedView, pollDelay, requestPreview } from "./converted";
import PdfView from "./PdfView";
import type { Source } from "./source";
import type { ViewerItem } from "./types";

function runs(list: Run[]): ReactNode[] {
  return list.map((r, i) => {
    let node: ReactNode = r.text;
    if (r.i) node = <em>{node}</em>;
    if (r.b) node = <strong>{node}</strong>;
    return <Fragment key={i}>{node}</Fragment>;
  });
}

/** Opeenvolgende lijstitems in één lijst; de rest los. */
function groupBlocks(blocks: OfficeBlock[]): (OfficeBlock | Extract<OfficeBlock, { type: "li" }>[])[] {
  const out: (OfficeBlock | Extract<OfficeBlock, { type: "li" }>[])[] = [];
  for (const block of blocks) {
    const last = out[out.length - 1];
    if (block.type === "li" && Array.isArray(last) && last[0].ordered === block.ordered) last.push(block);
    else out.push(block.type === "li" ? [block] : block);
  }
  return out;
}

function Table({ rows, header = false }: { rows: string[][]; header?: boolean }) {
  const width = Math.max(0, ...rows.map((r) => r.length));
  return (
    <div className="table-wrap">
      <table className="office-table">
        {header && (
          <thead>
            <tr>
              <th aria-hidden="true" />
              {Array.from({ length: width }, (_, i) => (
                <th key={i} scope="col">
                  {columnName(i)}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {header && <th scope="row">{i + 1}</th>}
              {Array.from({ length: width }, (_, j) => (
                <td key={j}>{row[j] ?? ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 0 -> A, 25 -> Z, 26 -> AA */
export function columnName(i: number): string {
  let n = i + 1;
  let name = "";
  while (n > 0) {
    const rest = (n - 1) % 26;
    name = String.fromCharCode(65 + rest) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

function DocumentBody({ doc }: { doc: Extract<OfficeDoc, { type: "document" }> }) {
  return (
    <article className="office-doc">
      {groupBlocks(doc.blocks).map((block, i) => {
        if (Array.isArray(block)) {
          const List = block[0].ordered ? "ol" : "ul";
          return (
            <List key={i}>
              {block.map((li, j) => (
                <li key={j} className={`level-${Math.min(li.level, 5)}`}>
                  {runs(li.runs)}
                </li>
              ))}
            </List>
          );
        }
        if (block.type === "h") {
          const H = `h${Math.min(6, Math.max(1, block.level) + 1)}` as "h2";
          return <H key={i}>{runs(block.runs)}</H>;
        }
        if (block.type === "table") return <Table key={i} rows={block.rows} />;
        if (block.type === "p") return <p key={i}>{runs(block.runs)}</p>;
        return null;
      })}
    </article>
  );
}

function SheetBody({ doc }: { doc: Extract<OfficeDoc, { type: "sheet" }> }) {
  const [current, setCurrent] = useState(0);
  const sheet = doc.sheets[current];
  if (!sheet) return <p className="muted">{t("viewer.office.empty")}</p>;
  const id = (i: number) => String(i);
  return (
    <div className="office-sheet">
      {doc.sheets.length > 1 && (
        <Tabs
          label={t("viewer.office.sheets")}
          idPrefix="blad"
          tabs={doc.sheets.map((s, i) => ({ id: id(i), label: s.name || t("viewer.office.sheet", { n: i + 1 }) }))}
          current={id(current)}
          onSelect={(v: string) => setCurrent(Number(v))}
        />
      )}
      <div {...(doc.sheets.length > 1 ? tabPanelProps("blad", id(current)) : {})}>
        {sheet.rows.length ? <Table rows={sheet.rows} header /> : <p className="muted">{t("viewer.office.emptySheet")}</p>}
        {sheet.truncated && <p className="muted">{t("viewer.office.sheetTruncated")}</p>}
      </div>
    </div>
  );
}

function SlidesBody({ doc }: { doc: Extract<OfficeDoc, { type: "slides" }> }) {
  const ratio = doc.size && doc.size[1] > 0 ? doc.size[0] / doc.size[1] : 16 / 9;
  return (
    <ol className={`office-slides${ratio < 1.5 ? " narrow" : ""}`}>
      {doc.slides.map((slide, i) => (
        <li key={i} className="office-slide">
          <span className="muted office-slide-n">{t("viewer.office.slide", { n: i + 1 })}</span>
          {slide.title && <h3>{slide.title}</h3>}
          {slide.bullets.length > 0 && (
            <ul>
              {slide.bullets.map((b, j) => (
                <li key={j} className={`level-${Math.min(b.level, 5)}`}>
                  {b.text}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}

/** Het label bij de pagina's van een omgezet document, met de uitleg ook voor schermlezers. */
function ConvertedNote(): JSX.Element {
  const hint = t("viewer.converted.labelHint");
  return (
    <span className="pages-note" title={hint}>
      {t("viewer.converted.label")}
      <span className="sr-only">: {hint}</span>
    </span>
  );
}

/** Bij pdf_state ask: de knop die een voorbeeld met opmaak vraagt. */
function AskPreview({ item, onAsked }: { item: ViewerItem; onAsked(): void }): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function ask() {
    setBusy(true);
    setError(null);
    try {
      await requestPreview(item);
      onAsked();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="office-note office-ask">
      <p>{t("viewer.converted.askHint")}</p>
      <Button icon="document" busy={busy} onClick={() => void ask()}>
        {t("viewer.converted.ask")}
      </Button>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

export default function OfficeView({ item, detail, source }: { item: ViewerItem; detail: ViewerDetail; source: Source }): JSX.Element {
  const [info, setInfo] = useState<DocumentInfo | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  // Elke keer opnieuw kijken (zolang de PDF gemaakt wordt, of na de knop) is een nieuwe ronde
  const [round, setRound] = useState(0);
  const polls = useRef(0);
  const shown = useRef(false);

  // Opnieuw zodra de preview klaar is (de inhoud komt mee met de preview) en zodra de PDF van
  // de editor er is (fase 4)
  const pdfState = detail.meta.pdf_state;
  useEffect(() => {
    let live = true;
    source.document(item).then(
      (d) => {
        if (!live) return;
        shown.current = true;
        setInfo(d);
      },
      (e: unknown) => {
        // Lukt opnieuw kijken niet, dan blijft staan wat er stond
        if (live && !shown.current) setError(errorText(e));
      },
    );
    return () => {
      live = false;
    };
  }, [item, source, detail.state, pdfState, round]);

  const view = info ? convertedView(info, source.kind === "user") : null;
  // Zolang de PDF gemaakt wordt: af en toe kijken, steeds minder vaak, en na ruim tien minuten
  // niet meer (Sluiten en weer openen kijkt opnieuw)
  useEffect(() => {
    if (view !== "pending") return;
    const delay = pollDelay(polls.current);
    if (delay === null) return;
    const timer = setTimeout(() => {
      polls.current += 1;
      setRound((r) => r + 1);
    }, delay);
    return () => clearTimeout(timer);
  }, [info, view, round]);

  if (info && view === "pages") return <PdfView item={item} source={source} initial={info} note={<ConvertedNote />} />;

  const doc: OfficeDoc | null | undefined = info?.office;
  // Een doc, xls, ppt of rtf heeft geen eigen inhoud: dan alleen de melding of de knop
  const waiting = view === "pending" || view === "ask";
  let body: ReactNode;
  if (error) body = <p role="alert">{error}</p>;
  else if (info === undefined) body = <Spinner label={t("viewer.loading")} />;
  else if (!doc)
    body = waiting ? null : (
      <p className="muted">
        {detail.state === "pending" ? t("viewer.pending") : detail.state === "failed" ? t("viewer.failed") : t("viewer.office.none")}
      </p>
    );
  else if (doc.type === "document") body = <DocumentBody doc={doc} />;
  else if (doc.type === "sheet") body = <SheetBody doc={doc} />;
  else body = <SlidesBody doc={doc} />;
  // Het vereenvoudigde voorbeeld zegt wat het is; niet zolang onbekend is wat er komt
  const simple = (info !== undefined || error !== null) && !(waiting && !doc);

  return (
    <div className="viewer-scroll office">
      {view === "pending" && (
        <p className="office-note" role="status">
          {t("viewer.converted.pending")}
        </p>
      )}
      {view === "ask" && (
        <AskPreview
          item={item}
          onAsked={() => {
            polls.current = 0;
            setRound((r) => r + 1);
          }}
        />
      )}
      {simple && <p className="office-note">{t("viewer.office.simplified")}</p>}
      {body}
      {doc?.truncated && <p className="muted">{t("viewer.office.truncated")}</p>}
    </div>
  );
}
