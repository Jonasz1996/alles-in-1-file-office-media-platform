// Wat de viewer van de server krijgt (routers/viewer.py): het volledige detail van één bestand,
// de pagina's van een PDF en de inhoud van een kantoordocument.

import type { Preview, PreviewMeta } from "../api";

/** EXIF van een foto (P5 vult het); nooit een locatie. */
export interface Exif {
  make?: string;
  model?: string;
  lens?: string;
  fnumber?: number | string;
  exposure?: number | string;
  iso?: number | string;
  focal?: number | string;
  focal35?: number | string;
  flash?: string | boolean;
  bias?: number | string;
  orientation?: number;
}

export interface ViewerMeta extends PreviewMeta {
  /** Tekst: de codering waarin de server de eerste regels las */
  encoding?: string;
  /** Een GIF, WebP of APNG die beweegt */
  animated?: boolean;
  video?: { codec?: string; profile?: string; pix_fmt?: string };
  audio?: { codec?: string; channels?: number; sample_rate?: number };
  container?: string;
  exif?: Exif;
  /** Kantoordocument: het soort inhoud in doc.json */
  office?: "document" | "sheet" | "slides";
  author?: string;
  format?: string;
}

export interface ViewerDetail {
  state: Preview["state"];
  thumb: string | null;
  large: string | null;
  /** 2048 px, alleen voor wat de browser zelf niet kan tonen (HEIC, HEIF, TIFF) */
  display: string | null;
  meta: ViewerMeta;
  /** De opnamedatum als kloktijd (opgeslagen als UTC, zo ook tonen) */
  taken_at: string | null;
}

export interface TocEntry {
  title: string;
  page: number;
  level: number;
}

export interface Run {
  text: string;
  b?: boolean;
  i?: boolean;
}

export type OfficeBlock =
  | { type: "h"; level: number; runs: Run[] }
  | { type: "p"; runs: Run[] }
  | { type: "li"; level: number; ordered: boolean; runs: Run[] }
  | { type: "table"; rows: string[][] };

export type OfficeDoc =
  | { type: "document"; blocks: OfficeBlock[]; truncated: boolean }
  | { type: "sheet"; sheets: { name: string; rows: string[][]; truncated: boolean }[]; truncated: boolean }
  | {
      type: "slides";
      size: [number, number] | null;
      slides: { title: string; bullets: { text: string; level: number }[] }[];
      truncated: boolean;
    };

export interface DocumentInfo {
  kind: "pdf" | "office";
  pages: number | null;
  /** Per pagina [breedte, hoogte] in punten */
  page_sizes: [number, number][] | null;
  toc: TocEntry[];
  labels: string[] | null;
  widths: number[];
  /** Met {n} en {w} in te vullen; bevat het token of de sleutel van de link */
  page_url: string | null;
  /** Zo lang (s) werkt page_url */
  token_expires_in: number;
  office: OfficeDoc | null;
}

/** Het adres van pagina n (vanaf 1), w pixels breed. */
export function pageHref(template: string, n: number, w: number): string {
  return template.replace("{n}", String(n)).replace("{w}", String(w));
}
