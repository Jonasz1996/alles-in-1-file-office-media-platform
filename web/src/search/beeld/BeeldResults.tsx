// Het raster van zoeken op beeld op /zoeken (fase 6, X3; Keuze 81: "Z4 (de keuze), X3 (het
// raster)"). In de stand beeld (?mode=image) toont de pagina van Z4 dit raster in plaats van de
// lijst; het leest q en like zelf uit de URL. Z0b zette de plek (review contract F3); Z4 laadt dit
// bestand lazy, met het gebied beeld (≤ 15 kB in budget.mjs). Het contract: de default export
// zonder props, een eigen chunk.
//
// Eerst de stand (GET /photos/beeld, status.ts). Kent de server zoeken op beeld niet (501: de
// analyse draait niet), dan staat hier zoals in 0.8.0 één zin zonder versienummer; sinds 0.8.1
// (I2) dat het uitstaat en dat de beheerder het kan aanzetten (beeld.later; de uitleg bij het veld,
// ImageHint, vraagt dezelfde stand). Anders zoekt het met
// GET /search?mode=image (beeld.ts):
// - uit (jij of de beheerder): alleen de melding, geen vraag aan /search;
// - je foto's worden nog bekeken: de melding met hoeveel procent klaar;
// - de terugval (te traag, geen container, geen model): de melding, en de treffers op naam en
//   tekst in hetzelfde raster;
// - niets gevonden, of misschien meer zonder filter.
// De meldingen staan in één role="status". Het raster is een role="grid" met een aria-label; elke
// foto heeft de naam en de datum als tekst. "Meer foto's laden" haalt de volgende 48; de focus gaat
// dan naar de eerste nieuwe foto (zoals de lijst van Z4), ook als de knop daarna wegvalt.
//
// Toetsenbord (Keuze 81, docs/fase-6/toetsenbord-x3.md): de pijlen naar de foto ernaast, erboven
// of eronder; Home en End de eerste of de laatste geladen foto; Enter opent de viewer (en terug
// komt op de foto die je het laatst zag); Esc terug naar het veld.

import { type JSX, type KeyboardEvent, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, type FileNode, type Preview } from "../../api";
import { previewCache, usePreviewRecord } from "../../browser/previews";
import { useCurrentUser } from "../../currentUser";
import { errorText, fmt, t } from "../../i18n";
import { useLocation } from "../../router";
import { Thumb } from "../../Thumb";
import { Button } from "../../ui/Button";
import { keyToMove } from "../../ui/keys";
import { useViewerParam, Viewer } from "../../viewer";
import { search } from "../api";
import { canSearch, queryHref, readQuery } from "../query";
import type { Hit, SearchPage } from "../types";
import { columnsFor, filtered, gridLabel, imageParams, moveInGrid, nextImageParams, type Notice, notices, rows } from "./beeld";
import { forgetBeeld, type Known, useBeeld } from "./status";
import "./beeld.css";

/** Een cel is minstens zo breed, met zoveel ruimte ertussen (zoals het raster van Bestanden). */
const CELL_MIN = 160;
const GAP = 12;

/** De treffers van één vraag, over alle geladen pagina's. */
interface Results {
  key: string;
  hits: Hit[];
  last: SearchPage;
}

/** Na "Meer foto's laden": de focus naar deze foto, in het raster van deze vraag. */
interface Jump {
  key: string;
  at: number;
}

/** Een fout bij een vraag: de code voor een eigen melding, anders de tekst. */
interface Failure {
  key: string;
  code: string | null;
  text: string;
}

export default function BeeldResults(): JSX.Element | null {
  const location = useLocation();
  // ?bestand= (de viewer) hoort niet bij de vraag
  const key = queryHref(readQuery(location.search));
  const query = useMemo(() => readQuery(key.slice(key.indexOf("?") + 1)), [key]);
  const user = useCurrentUser();
  const known = useBeeld(user?.id ?? null);
  const [results, setResults] = useState<Results | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [jump, setJump] = useState<Jump | null>(null);
  const searches = useRef(0);

  const state = known?.state;
  const off = state === "off";
  const blocked = state === "uit" || state === "beheer_uit";
  useEffect(() => {
    const seq = ++searches.current;
    setLoadingMore(false);
    setFailure(null);
    if (!known || off || blocked || !canSearch(query)) return;
    search(imageParams(query)).then(
      (page) => {
        if (seq === searches.current) setResults({ key, hits: page.results, last: page });
      },
      (e: unknown) => {
        if (seq !== searches.current) return;
        const code = e instanceof ApiError ? (e.code ?? null) : null;
        // Uit gezet (hier of in een ander tabblad): de stand opnieuw, die zegt door wie
        if (code === "beeld_off") forgetBeeld();
        setFailure({ key, code, text: errorText(e) });
      },
    );
  }, [key, query, known, off, blocked]);

  const at = stage(known);
  if (at === "wacht") return null;
  if (at === "later") return <BeeldLater />;

  const current = results?.key === key ? results : null;
  const failed = failure?.key === key ? failure : null;
  const hits = current?.hits ?? [];

  function loadMore(): void {
    const next = current && nextImageParams(query, current.last);
    if (!current || !next) return;
    const seq = searches.current;
    setLoadingMore(true);
    search(next).then(
      (page) => {
        if (seq !== searches.current) return;
        setLoadingMore(false);
        const merged = appendNew(current.hits, page.results);
        setResults((r) => (r && r.key === current.key ? { ...r, hits: merged, last: page } : r));
        // De focus naar de eerste nieuwe foto; zonder nieuwe blijft hij op de knop
        if (merged.length > current.hits.length) setJump({ key: current.key, at: current.hits.length });
      },
      (e: unknown) => {
        if (seq !== searches.current) return;
        setLoadingMore(false);
        setFailure({ key, code: e instanceof ApiError ? (e.code ?? null) : null, text: errorText(e) });
      },
    );
  }

  const list = notices({
    known,
    last: blocked ? null : (current?.last ?? null),
    count: hits.length,
    filtered: filtered(query),
    code: failed?.code ?? null,
  });
  const label = gridLabel(query);
  const more = !blocked && current && nextImageParams(query, current.last) !== null;
  return (
    <>
      <BeeldNotices list={list} />
      {failed && failed.code !== "beeld_off" && failed.code !== "beeld_no_vector" && <p role="alert">{failed.text}</p>}
      {!blocked && hits.length > 0 && (
        <Grid key={key} hits={hits} label={t(label.key, label.vars)} jump={jump?.key === key ? jump : null} />
      )}
      {more && (
        <p>
          <Button onClick={loadMore} busy={loadingMore}>
            {loadingMore ? t("beeld.grid.loadingMore") : t("beeld.grid.more")}
          </Button>
        </p>
      )}
    </>
  );
}

/** Wat de plek toont: niets zolang de stand er niet is, de zin van 0.8.0 als de server zoeken op
 * beeld niet kent (de analyse staat uit), anders het raster met de meldingen. */
export function stage(known: Known | null): "wacht" | "later" | "raster" {
  if (known === null) return "wacht";
  return known.state === "off" ? "later" : "raster";
}

/** Precies wat 0.8.0 in de stand beeld toonde: één zin, zonder versienummer. */
export function BeeldLater(): JSX.Element {
  return <p className="muted">{t("beeld.later")}</p>;
}

/** De volgende pagina erbij, zonder een foto die er al staat. */
function appendNew(hits: readonly Hit[], page: readonly Hit[]): Hit[] {
  const seen = new Set(hits.map((h) => h.node.id));
  return [...hits, ...page.filter((h) => !seen.has(h.node.id))];
}

/** De meldingen in één role="status" (ook leeg: dan hoort een schermlezer de volgende). */
export function BeeldNotices({ list }: { list: readonly Notice[] }): JSX.Element {
  return (
    <div role="status" className="search-notices">
      {list.map((n) => (
        <p key={n.key} className="muted">
          {t(n.key, n.vars)}
        </p>
      ))}
    </div>
  );
}

/** Het raster: rijen van cellen, de cel met de focus is de enige tabstop. */
function Grid({ hits, label, jump }: { hits: readonly Hit[]; label: string; jump: Jump | null }): JSX.Element {
  const nodes = useMemo(() => hits.map((h) => h.node), [hits]);
  const previews = usePreviewRecord(nodes);
  const grid = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(1);
  const [focus, setFocus] = useState(0);
  const [viewing, setViewing] = useViewerParam();
  /** De foto die de viewer het laatst toonde: daar komt de focus op terug */
  const last = useRef<string | null>(null);

  useLayoutEffect(() => {
    const el = grid.current;
    if (!el) return;
    const apply = () => setColumns(columnsFor(el.clientWidth, CELL_MIN, GAP));
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // De focus meteen: elke cel staat al in de pagina (het raster is geen virtuele lijst). Een focus
  // een frame later kon na de volgende toets vallen: Esc vlak na het sluiten van de viewer zette
  // de focus in het veld, en dat frame zette hem terug op de foto
  const moveTo = useCallback((i: number) => {
    setFocus(i);
    grid.current?.querySelector<HTMLElement>(`[data-index="${i}"]`)?.focus();
  }, []);

  // Kwam je hier zonder focus (vanuit "Meer zoals deze" in de viewer ging de viewer dicht, en
  // daarmee de foto die de focus had), dan op de eerste foto. Een focus in het veld of op de kop
  // blijft waar hij is
  useEffect(() => {
    const active = document.activeElement;
    if (!active || active === document.body) moveTo(0);
  }, [moveTo]);

  // Na "Meer foto's laden": de eerste nieuwe foto
  useEffect(() => {
    if (jump) moveTo(jump.at);
  }, [jump, moveTo]);

  const files = useMemo(() => nodes.filter((n) => n.kind === "file"), [nodes]);
  const viewIndex = viewing ? files.findIndex((n) => n.id === viewing) : -1;
  if (viewIndex >= 0) last.current = files[viewIndex].id;

  // De viewer ging dicht: de focus op de foto die je het laatst zag
  useEffect(() => {
    if (viewing !== null || last.current === null) return;
    const i = nodes.findIndex((n) => n.id === last.current);
    last.current = null;
    if (i >= 0) moveTo(i);
  }, [viewing, nodes, moveTo]);

  function open(i: number): void {
    const node = nodes[i];
    if (node?.kind === "file") setViewing(node.id);
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>): void {
    if (e.ctrlKey || e.metaKey) return;
    const m = keyToMove(e);
    if (m && !e.shiftKey) {
      e.preventDefault();
      moveTo(moveInGrid(focus, m, nodes.length, columns));
    } else if (e.key === "Enter") {
      e.preventDefault();
      open(focus);
    } else if (e.key === "Escape") {
      e.preventDefault();
      document.querySelector<HTMLInputElement>(".search-form input[type=search]")?.focus();
    }
  }

  const template = { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` };
  const at = Math.min(focus, nodes.length - 1);
  return (
    <>
      <div ref={grid} role="grid" aria-label={label} aria-rowcount={Math.ceil(nodes.length / columns)} className="beeld-grid" onKeyDown={onKeyDown}>
        {rows(nodes.length, columns).map((row, r) => (
          <div key={r} role="row" aria-rowindex={r + 1} className="beeld-row" style={template}>
            {row.map((i) => (
              <Cell
                key={nodes[i].id}
                node={nodes[i]}
                index={i}
                tabbable={i === at}
                previews={previews}
                onFocus={() => setFocus(i)}
                onOpen={() => {
                  setFocus(i);
                  open(i);
                }}
              />
            ))}
          </div>
        ))}
      </div>
      {viewIndex >= 0 && (
        <Suspense fallback={null}>
          <Viewer
            items={files}
            index={viewIndex}
            onIndexChange={(i) => setViewing(files[i].id)}
            onClose={() => setViewing(null)}
          />
        </Suspense>
      )}
    </>
  );
}

function Cell({
  node,
  index,
  tabbable,
  previews,
  onFocus,
  onOpen,
}: {
  node: FileNode;
  index: number;
  tabbable: boolean;
  previews: Record<string, Preview>;
  onFocus(): void;
  onOpen(): void;
}): JSX.Element {
  return (
    <div role="gridcell" data-index={index} tabIndex={tabbable ? 0 : -1} className="beeld-cell" onFocus={onFocus} onClick={onOpen}>
      <Thumb node={node} preview={previews[node.id]} pending={previewCache.pending(node.id, node.etag)} size={180} />
      <span className="beeld-name">{node.name}</span>
      <span className="beeld-date muted">{fmt.date(node.modified_at)}</span>
    </div>
  );
}
