// Typen voor de vertalingen. MessageKey komt uit de Nederlandse woordenboeken (de bron), en
// uitsluitend via `import type`: dit bestand laadt geen enkel woordenboek in de bundel.

import type account from "./areas/account.nl";
import type admin from "./areas/admin.nl";
import type auth from "./areas/auth.nl";
import type browser from "./areas/browser.nl";
import type common from "./areas/common.nl";
import type office from "./areas/office.nl";
import type photos from "./areas/photos.nl";
import type publicArea from "./areas/public.nl";
import type pwa from "./areas/pwa.nl";
import type sharing from "./areas/sharing.nl";
import type shell from "./areas/shell.nl";
import type trash from "./areas/trash.nl";
import type upload from "./areas/upload.nl";
import type versions from "./areas/versions.nl";
import type viewer from "./areas/viewer.nl";

export type Locale = "nl" | "en";

export type Area =
  | "common"
  | "shell"
  | "auth"
  | "pwa"
  | "browser"
  | "upload"
  | "viewer"
  | "public"
  | "photos"
  | "admin"
  | "account"
  | "sharing"
  | "trash"
  | "versions"
  | "office";

/** Meervoudsvormen (Intl.PluralRules); "other" is altijd nodig. Een object met de sleutel
 * "other" is daarom altijd een meervoud, nooit een groep sleutels. */
export interface PluralForms {
  readonly zero?: string;
  readonly one?: string;
  readonly two?: string;
  readonly few?: string;
  readonly many?: string;
  readonly other: string;
}

/** Een woordenboek zoals het in het geheugen staat. */
export interface Messages {
  readonly [key: string]: string | PluralForms | Messages;
}

/** De vorm van een vertaling van woordenboek T: dezelfde sleutels, met gewone strings. Een
 * ontbrekende of extra sleutel in een .en.ts-bestand is zo een tsc-fout. */
export type Translation<T> = {
  readonly [K in keyof T]: T[K] extends string
    ? string
    : T[K] extends { readonly other: string }
      ? PluralForms
      : Translation<T[K]>;
};

/** Alle sleutels van een woordenboek als punt-paden: "selected", "nav.files". */
type Leaves<T, P extends string> = {
  [K in keyof T & string]: T[K] extends string
    ? `${P}${K}`
    : T[K] extends { readonly other: string }
      ? `${P}${K}`
      : Leaves<T[K], `${P}${K}.`>;
}[keyof T & string];

export interface Dictionaries {
  common: typeof common;
  shell: typeof shell;
  auth: typeof auth;
  pwa: typeof pwa;
  browser: typeof browser;
  upload: typeof upload;
  viewer: typeof viewer;
  public: typeof publicArea;
  photos: typeof photos;
  admin: typeof admin;
  account: typeof account;
  sharing: typeof sharing;
  trash: typeof trash;
  versions: typeof versions;
  office: typeof office;
}

/** "browser.title", "common.close", "shell.nav.files": gebied, dan het pad in het woordenboek. */
export type MessageKey = { [A in Area]: Leaves<Dictionaries[A], `${A}.`> }[Area];

/**
 * Een foutcatalogus (i18n/errors/<gebied>.ts): de Engelse tekst per foutcode van de API. De
 * catalogi schrijven dit type uit (`satisfies Record<string, { en: string }>`, zonder imports):
 * api/tests/test_error_catalog.py leest ze met een reguliere expressie.
 */
export type ErrorCatalog = Record<string, { en: string }>;
