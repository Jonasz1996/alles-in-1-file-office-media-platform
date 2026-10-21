// API-client. Het access-token staat alleen in het geheugen; het refresh-token zit in een
// httpOnly-cookie, zodat JavaScript het niet kan lezen. Bij een 401 wordt één keer ververst.

import { fmt } from "./i18n/fmt";
import type { Locale } from "./i18n/types";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "user" | "guest";
  quota_bytes: number;
  used_bytes: number;
  root_id: string;
  totp_enabled: boolean;
  /** 2FA is verplicht en staat voor deze gebruiker nog niet aan: alleen 2FA aanzetten werkt. */
  totp_setup_required: boolean;
  /** Na te veel foute codes: tot wanneer de tweede factor geblokkeerd is (ISO-tijd). */
  totp_locked_until: string | null;
  /** De taal die bij het account hoort; null = die van het apparaat. Oudere servers sturen hem niet. */
  locale?: Locale | null;
  /** Geblokkeerd door een beheerder (alleen in de gebruikerslijst van beheer). */
  disabled?: boolean;
}

export interface Tokens {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: User;
}

export interface FileNode {
  id: string;
  parent_id: string | null;
  name: string;
  /** album: een album (fase 3), staat buiten de boom (parent_id null). */
  kind: "file" | "dir" | "album";
  mime: string | null;
  size: number;
  etag: string;
  created_at: string;
  modified_at: string;
  deleted_at: string | null;
  /** Alleen de eigen tags van wie ingelogd is. */
  tags: TagRef[];
  /** Wat jij ermee mag. Oudere servers sturen het niet: dan is het van jou (permissionOf). */
  permission?: Permission;
  /** Van wie het is */
  owner?: { id: string; display_name: string };
  /** Er staat minstens één deling of link op (alleen voor de eigenaar). */
  shared?: boolean;
}

export interface Listing {
  folder: FileNode;
  path: FileNode[];
  children: FileNode[];
}

// --- F: bestanden (kopiëren, versies, zoeken, tags) ---

export interface TagRef {
  id: string;
  name: string;
  /** "#rrggbb" of leeg */
  color: string;
}

export interface Tag extends TagRef {
  /** Op hoeveel mappen en bestanden (niet in de prullenbak) de tag staat. */
  count: number;
}

export interface FileVersion {
  id: string;
  size: number;
  /** Wanneer deze inhoud gemaakt werd */
  created_at: string;
  /** Wanneer en door wie hij vervangen werd */
  replaced_at: string;
  replaced_by: { id: string; display_name: string } | null;
}

export type Category = "folder" | "image" | "video" | "audio" | "pdf" | "document" | "archive" | "other";

export interface SearchQuery {
  q?: string;
  type?: Category | "";
  tag?: string;
  /** Alleen in deze map (en alles eronder) */
  in?: string;
  limit?: number;
}

export interface SearchHit {
  node: FileNode;
  /** Van de hoofdmap (naam "") tot en met de map waar het in staat */
  path: { id: string; name: string }[];
}

export interface SearchResult {
  results: SearchHit[];
  more: boolean;
}

/** Querystring voor /api/v1/search; lege velden laat hij weg. */
export function searchParams(query: SearchQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && String(value).trim() !== "") params.set(key, String(value).trim());
  }
  return params.toString();
}

// --- einde F ---

export interface NodeStatus {
  name: string;
  ip: string;
  checks: Record<string, boolean>;
  db_role: string | null;
  disk: { total_bytes: number; used_bytes: number; free_bytes: number } | null;
}

export interface SystemStatus {
  status: "ok" | "degraded" | "down";
  nodes: NodeStatus[];
  database: { name: string; role: string; state: string; lag_bytes: number | null }[];
  messages: string[];
  users: number;
  files: number;
  stored_bytes: number;
  logical_bytes: number;
}

export interface HttpsStatus {
  domain: string;
  email: string;
  staging: boolean;
  state: "uit" | "bezig" | "actief" | "fout";
  error: string | null;
  active: boolean;
  serving: boolean;
  /** De https-poort kon niet open (bijv. bezet); http blijft werken. */
  listener_error: string | null;
  cert_domain: string | null;
  expires_at: string | null;
  renew_at: string | null;
  last_attempt: string | null;
  next_check: string | null;
}

export interface SecurityConfig {
  require_2fa: boolean;
}

export interface TotpSetup {
  secret: string;
  uri: string;
  /** data-URI met de QR-code */
  qr: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** Machineleesbare reden, bijv. "totp_required" bij inloggen met 2FA. */
    public code?: string,
    /** Gegevens bij de code, bijv. {until, seconds} bij totp_locked of {field} bij invalid_input. */
    public params?: Record<string, unknown>,
  ) {
    super(message);
  }
}

/** De tekst bij geen verbinding (fetch gooit); in een andere taal vertaalt errorText op code. */
export const NETWORK_MESSAGE = "Geen verbinding met de server. Probeer het opnieuw.";

/** Een mislukte fetch (geen verbinding: een TypeError) als ApiError met code "network", zodat
 * een programmeerfout (ook vaak een TypeError) er niet op lijkt. Afbreken (AbortError) en
 * andere fouten gaan ongewijzigd door. */
export function networkError(e: unknown): unknown {
  return e instanceof TypeError ? new ApiError(0, NETWORK_MESSAGE, "network") : e;
}

/** fetch, maar een mislukte verbinding gooit ApiError(0, …, "network"). */
export async function fetchOrNetworkError(fetchFn: typeof fetch, ...args: Parameters<typeof fetch>): Promise<Response> {
  try {
    return await fetchFn(...args);
  } catch (e) {
    throw networkError(e);
  }
}

/** Een fout zonder bruikbare tekst van de server: "Fout 500", in het Engels "Error 500". */
function httpError(status: number): ApiError {
  return new ApiError(status, `Fout ${status}`, "http_error", { status });
}

/**
 * Leest de fout uit een antwoord: {"detail": {"message", "code", "params"?}} (ook de 422 met
 * "errors" erbij), het oude {"detail": "..."} en de standaard-422 van FastAPI (een lijst).
 */
export function errorFromBody(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return new ApiError(status, detail);
  if (Array.isArray(detail)) {
    return detail[0]?.msg ? new ApiError(status, String(detail[0].msg)) : httpError(status);
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: unknown; code?: unknown; params?: unknown };
    const params =
      d.params && typeof d.params === "object" && !Array.isArray(d.params)
        ? (d.params as Record<string, unknown>)
        : undefined;
    if (!(typeof d.message === "string" && d.message) && typeof d.code !== "string") return httpError(status);
    return new ApiError(
      status,
      typeof d.message === "string" && d.message ? d.message : `Fout ${status}`,
      typeof d.code === "string" ? d.code : undefined,
      params,
    );
  }
  return httpError(status);
}

const TUS = { "Tus-Resumable": "1.0.0" };

/**
 * Wachttijden (ms) als een ander tabblad tegelijk met hetzelfde cookie ververste en won. Samen
 * net langer dan de marge van de server (REFRESH_RACE, 5 s): daarna is het hergebruik.
 */
const RACE_RETRIES = [500, 1500, 4000];

async function raced(res: Response): Promise<boolean> {
  if (res.status !== 401) return false;
  try {
    return errorFromBody(401, await res.clone().json()).code === "refresh_raced";
  } catch {
    return false;
  }
}

/** tus Upload-Metadata: sleutel en base64 van de UTF-8-waarde, gescheiden door komma's. */
export function tusMetadata(values: Record<string, string>): string {
  return Object.entries(values)
    .map(([k, v]) => `${k} ${btoa(String.fromCharCode(...new TextEncoder().encode(v)))}`)
    .join(",");
}

type Fetch = typeof fetch;

// --- S: delen ---

/** Wat iemand met een map of bestand mag. */
export type Permission = "owner" | "write" | "read";

/** Wat een deling toestaat; upload alleen bij een link. */
export type SharePermission = "read" | "write" | "upload";

export interface Person {
  id: string;
  display_name: string;
  email: string;
}

export interface GroupRef {
  id: string;
  name: string;
  member_count: number;
}

export interface Group extends GroupRef {
  members: Person[];
}

/** Zoeken naar mensen en groepen om mee te delen (nooit jezelf). */
export interface Directory {
  users: Person[];
  groups: GroupRef[];
}

export interface Share {
  id: string;
  node_id: string;
  type: "user" | "group" | "link";
  permission: SharePermission;
  created_at: string;
  expires_at: string | null;
  user: Person | null;
  group: GroupRef | null;
  /** url is het pad (/s/<token>); de link zelf is location.origin + url. */
  link: { url: string; label: string | null; has_password: boolean } | null;
}

export interface NewLink {
  permission: "read" | "upload";
  password?: string;
  expires_at?: string;
  label?: string;
}

/** expires_at null haalt de vervaldatum weg, password "" het wachtwoord (links). */
export interface ShareChange {
  permission?: SharePermission;
  expires_at?: string | null;
  password?: string;
  label?: string;
}

export interface SharedWithMe {
  share_id: string;
  node: FileNode;
  permission: "read" | "write";
  via: "user" | "group";
  group?: { id: string; name: string } | null;
  owner: { id: string; display_name: string };
  created_at: string;
}

/** Een map of bestand zoals een publieke link het laat zien: zonder eigenaar, ouder of tags. */
export interface PublicNode {
  id: string;
  name: string;
  kind: "file" | "dir";
  mime: string | null;
  size: number;
  etag: string;
  modified_at: string;
}

export interface PublicShare {
  /** album: een gedeeld album (fase 3, P5); de pagina toont dan PublicAlbum. */
  kind: "file" | "dir" | "album";
  name: string;
  permission: "read" | "upload";
  owner_name: string;
  expires_at: string | null;
  root: PublicNode;
}

export interface PublicListing {
  folder: PublicNode;
  /** Van de gedeelde map tot en met folder */
  path: PublicNode[];
  children: PublicNode[];
}

// --- einde S ---

/** Hoe de laatste /auth/refresh afliep (voor het inlogscherm en de schil). */
export interface LastRefresh {
  /** Uit de 401 (code not_logged_in, params.setup_required): moet het eerste account nog
   * gemaakt worden? null als de server het niet zei (of er geen refresh was). */
  setupRequired: boolean | null;
  /** De fout als refresh mislukte, bijv. 403 account_disabled; null als het lukte of nog niet liep. */
  error?: ApiError | null;
}

export class Api {
  private token: string | null = null;
  private refreshing: Promise<User | null> | null = null;
  /** Hoe de laatste refresh afliep. */
  lastRefresh: LastRefresh = { setupRequired: null, error: null };
  onLoggedOut: () => void = () => {};
  /** De server zegt dat eerst 2FA aan moet (bijv. omdat de beheerder het net verplicht maakte). */
  onSetupRequired: () => void = () => {};

  constructor(private fetchFn: Fetch = (...args) => fetch(...args)) {}

  get loggedIn(): boolean {
    return this.token !== null;
  }

  /** Het huidige access-token, voor verzoeken die niet via fetch gaan (uploads). */
  get accessToken(): string | null {
    return this.token;
  }

  /** Een verzoek met het token; geeft het Response (ook voor HEAD, streams en tus). Gooit een
   * ApiError als het antwoord geen 2xx is. Na een 401 wordt één keer ververst. */
  rawRequest(path: string, init: RequestInit = {}): Promise<Response> {
    return this.raw(path, init);
  }

  /** Een JSON-verzoek: body wordt JSON, het antwoord ook (undefined bij 204). */
  request<T>(method: string, path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (body !== undefined) headers.set("Content-Type", "application/json");
    return this.json<T>(path, {
      ...init,
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  private async raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
    const headers = new Headers(init.headers);
    const hadToken = this.token !== null;
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    const res = await fetchOrNetworkError(this.fetchFn, path, { ...init, headers, credentials: "same-origin" });
    if (res.status === 401 && retry && this.token && (await this.refresh())) {
      return this.raw(path, init, false);
    }
    if (!res.ok) {
      let body: unknown = null;
      try {
        body = await res.json();
      } catch {
        /* geen JSON */
      }
      // Een 401 terwijl we ingelogd waren (en verversen niet hielp): sessie is voorbij.
      // Bij een mislukte login (nog geen token) is er niets om uit te loggen.
      if (res.status === 401 && hadToken) {
        this.token = null;
        this.onLoggedOut();
      }
      const error = errorFromBody(res.status, body);
      if (res.status === 403 && error.code === "totp_setup_required") this.onSetupRequired();
      throw error;
    }
    return res;
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await this.raw(path, init);
    return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
  }

  private send<T>(method: string, path: string, body?: unknown): Promise<T> {
    return this.request<T>(method, path, body);
  }

  private accept(tokens: Tokens): User {
    this.token = tokens.access_token;
    return tokens.user;
  }

  /**
   * Probeert in te loggen met het refresh-cookie; geeft de gebruiker of null. Gelijktijdige
   * aanroepen delen één verzoek: elk refresh-token werkt maar één keer, en hetzelfde token
   * een tweede keer insturen trekt de hele login in.
   */
  refresh(): Promise<User | null> {
    this.refreshing ??= this.renew().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  private async renew(): Promise<User | null> {
    const wasLoggedIn = this.token !== null;
    const post = () =>
      fetchOrNetworkError(this.fetchFn, "/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" });
    let res = await post();
    // Tabbladen delen het cookie; ververste een ander tabblad net, dan heeft de browser even
    // later het nieuwe cookie uit dat antwoord. Was het geen eigen tabblad, dan valt de laatste
    // poging na de marge van de server en trekt die de login in (een gestolen token).
    for (const wait of RACE_RETRIES) {
      if (!(await raced(res))) break;
      await new Promise((resolve) => setTimeout(resolve, wait));
      res = await post();
    }
    if (!res.ok) {
      this.token = null;
      let body: unknown = null;
      try {
        body = await res.json();
      } catch {
        /* geen JSON */
      }
      const error = errorFromBody(res.status, body);
      const setup = error.params?.setup_required;
      this.lastRefresh = { setupRequired: typeof setup === "boolean" ? setup : null, error };
      // Er was een sessie en die is nu voorbij (bijv. uitgelogd in een ander tabblad). Ook
      // melden als dit niet via een gewoon verzoek liep (de upload ververst zelf), want
      // zonder token komt er daarna geen 401 meer die dat nog doet.
      if (wasLoggedIn) this.onLoggedOut();
      return null;
    }
    this.lastRefresh = { setupRequired: false, error: null };
    return this.accept((await res.json()) as Tokens);
  }

  setupRequired = async () =>
    (await this.json<{ setup_required: boolean }>("/api/v1/auth/status")).setup_required;
  setup = async (email: string, display_name: string, password: string) =>
    this.accept(await this.send<Tokens>("POST", "/api/v1/auth/setup", { email, display_name, password }));
  /** code: uit de authenticator-app of een herstelcode, als 2FA aan staat. */
  login = async (email: string, password: string, code?: string) =>
    this.accept(await this.send<Tokens>("POST", "/api/v1/auth/login", { email, password, code }));
  async logout(): Promise<void> {
    await this.send("POST", "/api/v1/auth/logout").catch(() => {});
    this.token = null;
  }
  me = () => this.json<User>("/api/v1/auth/me");
  /** De taal bij het account bewaren; null = die van het apparaat volgen. */
  setAccountLocale = (l: Locale | null) => this.send<User>("PATCH", "/api/v1/auth/me", { locale: l });

  totpSetup = () => this.send<TotpSetup>("POST", "/api/v1/auth/2fa/setup");
  totpEnable = async (code: string, password: string) =>
    (await this.send<{ recovery_codes: string[] }>("POST", "/api/v1/auth/2fa/enable", { code, password }))
      .recovery_codes;
  totpRecoveryCodes = async (code: string) =>
    (await this.send<{ recovery_codes: string[] }>("POST", "/api/v1/auth/2fa/recovery-codes", { code }))
      .recovery_codes;
  totpDisable = (password: string, code: string) =>
    this.send<void>("POST", "/api/v1/auth/2fa/disable", { password, code });

  // --- account: wachtwoord wijzigen, sessies per apparaat, wachtwoord instellen (beheer) ---
  /** Waar je ingelogd bent: één rij per login, de laatst actieve eerst. */
  sessions = () =>
    this.json<
      {
        /** De login (refresh-family) */
        id: string;
        /** Bijv. "Firefox op Windows" */
        device: string;
        ip: string | null;
        /** Eerste login van deze sessie */
        created_at: string;
        last_active_at: string;
        /** De sessie van deze app zelf */
        current: boolean;
      }[]
    >("/api/v1/auth/sessions");
  /** Logt die sessie uit. Is het deze sessie zelf, dan is de app daarna uitgelogd. */
  revokeSession = async (id: string, current = false) => {
    await this.send<void>("DELETE", `/api/v1/auth/sessions/${id}`);
    if (current) {
      await this.logout(); // ook het cookie weg
      this.onLoggedOut();
    }
  };
  revokeOtherSessions = () => this.send<void>("POST", "/api/v1/auth/sessions/revoke-others");
  /** code: uit de authenticator-app of een herstelcode, als 2FA aan staat. Andere sessies worden uitgelogd. */
  changePassword = (current_password: string, new_password: string, code?: string) =>
    this.send<void>("POST", "/api/v1/auth/password", { current_password, new_password, code });
  /** Alleen voor beheerders, niet voor je eigen account; logt die gebruiker overal uit. */
  setUserPassword = (userId: string, new_password: string) =>
    this.send<void>("PUT", `/api/v1/users/${userId}/password`, { new_password });

  root = () => this.json<Listing>("/api/v1/files/root");
  children = (id: string) => this.json<Listing>(`/api/v1/files/${id}/children`);
  mkdir = (parent: string, name: string) =>
    this.send<FileNode>("POST", `/api/v1/files/${parent}/folders`, { name });
  change = (id: string, change: { name?: string; parent_id?: string }) =>
    this.send<FileNode>("PATCH", `/api/v1/files/${id}`, change);
  trash = (id: string) => this.send<void>("DELETE", `/api/v1/files/${id}`);
  listTrash = () => this.json<FileNode[]>("/api/v1/trash");
  restore = (id: string) => this.send<FileNode>("POST", `/api/v1/files/${id}/restore`);
  purge = (id: string) => this.send<void>("DELETE", `/api/v1/trash/${id}`);
  downloadUrl = async (id: string, inline = false) => {
    const { url } = await this.send<{ url: string }>("POST", `/api/v1/files/${id}/download-link`);
    return inline ? `${url}&inline=true` : url;
  };

  // --- F: kopiëren, versies, zoeken, tags ---
  /** Kopie in map parentId; bestaat de naam daar al, dan krijgt de kopie "naam (1)". */
  copy = (id: string, parentId: string, name?: string) =>
    this.send<FileNode>("POST", `/api/v1/files/${id}/copy`, { parent_id: parentId, name });
  /** Nieuwe inhoud voor een bestaand bestand; de vorige blijft bewaard als versie. Via het
   * bestand zelf: zo kan het ook als alleen dit bestand met je gedeeld is (de map erboven zie
   * je dan niet). */
  replaceContent = (id: string, file: Blob) =>
    this.json<FileNode>(`/api/v1/files/${id}/content`, { method: "PUT", body: file });
  versions = (id: string) => this.json<FileVersion[]>(`/api/v1/files/${id}/versions`);
  restoreVersion = (id: string, versionId: string) =>
    this.send<FileNode>("POST", `/api/v1/files/${id}/versions/${versionId}/restore`);
  deleteVersion = (id: string, versionId: string) =>
    this.send<void>("DELETE", `/api/v1/files/${id}/versions/${versionId}`);
  versionDownloadUrl = async (id: string, versionId: string) =>
    (
      await this.send<{ url: string }>(
        "POST",
        `/api/v1/files/${id}/download-link?${new URLSearchParams({ version: versionId })}`,
      )
    ).url;
  search = (query: SearchQuery) => this.json<SearchResult>(`/api/v1/search?${searchParams(query)}`);
  tags = () => this.json<Tag[]>("/api/v1/tags");
  createTag = (name: string, color = "") => this.send<Tag>("POST", "/api/v1/tags", { name, color });
  changeTag = (id: string, change: { name?: string; color?: string }) =>
    this.send<Tag>("PATCH", `/api/v1/tags/${id}`, change);
  deleteTag = (id: string) => this.send<void>("DELETE", `/api/v1/tags/${id}`);
  /** Vervangt de tags op een map of bestand door deze set; geeft de tags die het nu heeft. */
  setTags = (id: string, tagIds: string[]) =>
    this.send<TagRef[]>("PUT", `/api/v1/files/${id}/tags`, { tag_ids: tagIds });
  // --- einde F ---

  // --- S: delen ---
  /** Mensen en groepen om mee te delen; q is een (deel van een) naam of e-mailadres. */
  directory = (q: string) => this.json<Directory>(`/api/v1/directory?${new URLSearchParams({ q })}`);
  /** De delingen en links van een map of bestand (alleen voor de eigenaar). */
  shares = (nodeId: string) => this.json<Share[]>(`/api/v1/files/${nodeId}/shares`);
  share = (
    nodeId: string,
    to: { user_id: string } | { group_id: string },
    permission: "read" | "write",
    expires_at?: string,
  ) => this.send<Share>("POST", `/api/v1/files/${nodeId}/shares`, { ...to, permission, expires_at });
  createLink = (nodeId: string, link: NewLink) => this.send<Share>("POST", `/api/v1/files/${nodeId}/links`, link);
  changeShare = (id: string, change: ShareChange) => this.send<Share>("PATCH", `/api/v1/shares/${id}`, change);
  /** Ook voor links: wie de link heeft, kan er daarna niet meer bij. */
  deleteShare = (id: string) => this.send<void>("DELETE", `/api/v1/shares/${id}`);
  sharedWithMe = () => this.json<SharedWithMe[]>("/api/v1/shared-with-me");
  groups = () => this.json<Group[]>("/api/v1/groups");
  createGroup = (name: string) => this.send<Group>("POST", "/api/v1/groups", { name });
  renameGroup = (id: string, name: string) => this.send<Group>("PATCH", `/api/v1/groups/${id}`, { name });
  deleteGroup = (id: string) => this.send<void>("DELETE", `/api/v1/groups/${id}`);
  /** Vervangt de leden door deze gebruikers. */
  setGroupMembers = (id: string, userIds: string[]) =>
    this.send<Group>("PUT", `/api/v1/groups/${id}/members`, { user_ids: userIds });
  // --- einde S ---

  adminStatus = () => this.json<SystemStatus>("/api/v1/admin/status");
  users = () => this.json<User[]>("/api/v1/users");
  addUser = (u: { email: string; display_name: string; password: string; role: string; quota_bytes: number }) =>
    this.send<User>("POST", "/api/v1/users", u);
  resetTotp = (userId: string) => this.send<void>("DELETE", `/api/v1/users/${userId}/2fa`);
  httpsStatus = () => this.json<HttpsStatus>("/api/v1/admin/https");
  httpsConfigure = (config: { domain: string; email: string; staging: boolean }) =>
    this.send<HttpsStatus>("PUT", "/api/v1/admin/https", config);
  httpsRenew = () => this.send<HttpsStatus>("POST", "/api/v1/admin/https/renew");
  security = () => this.json<SecurityConfig>("/api/v1/admin/security");
  setSecurity = (config: SecurityConfig) => this.send<SecurityConfig>("PUT", "/api/v1/admin/security", config);

  /** Maakt een hervatbare upload (tus) aan; geeft de URL om naartoe te sturen. Bij een leeg
   * bestand is het meteen klaar en komt nodeId terug. meta: extra Upload-Metadata, bijv.
   * lastmodified (File.lastModified in ms, als tekst) en conflict; filename en parent gaan altijd
   * voor. */
  async tusCreate(
    parent: string,
    name: string,
    size: number,
    meta?: Record<string, string>,
  ): Promise<{ url: string; nodeId: string | null }> {
    const res = await this.raw("/api/v1/tus", {
      method: "POST",
      headers: {
        ...TUS,
        "Upload-Length": String(size),
        "Upload-Metadata": tusMetadata({ ...meta, filename: name, parent }),
      },
    });
    const url = res.headers.get("Location");
    if (!url) throw new ApiError(res.status, "Geen Location in het antwoord");
    return { url, nodeId: res.headers.get("Platform-Node-Id") };
  }
  /** Hoeveel bytes de server al heeft, en het bestand als de upload al klaar is. */
  async tusStatus(url: string): Promise<{ offset: number; nodeId: string | null }> {
    let res: Response;
    try {
      res = await this.raw(url, { method: "HEAD", headers: TUS });
    } catch (e) {
      // HEAD heeft geen body: de server rondde de upload hier af en dat lukte niet.
      // 409 kan dan alleen een bezette naam zijn, 413 alleen het quotum.
      if (e instanceof ApiError && e.status === 409) {
        throw new ApiError(409, "Er bestaat al iets met deze naam", "name_taken");
      }
      if (e instanceof ApiError && e.status === 413) throw new ApiError(413, "Onvoldoende quotum", "no_room");
      throw e;
    }
    return { offset: Number(res.headers.get("Upload-Offset") ?? 0), nodeId: res.headers.get("Platform-Node-Id") };
  }
  async tusCancel(url: string): Promise<void> {
    await this.raw(url, { method: "DELETE", headers: TUS });
  }

  // --- J: previews, achtergrondtaken en live gebeurtenissen ---
  /** Previews van (hooguit 500) bestanden; wat niet bestaat of geen bestand is, ontbreekt. */
  previews = (nodeIds: string[]) =>
    this.send<Record<string, Preview>>("POST", "/api/v1/previews", { node_ids: nodeIds });
  adminJobs = () => this.json<JobsOverview>("/api/v1/admin/jobs");
  retryJob = (id: string) => this.send<void>("POST", `/api/v1/admin/jobs/${id}/retry`);
  retryFailedJobs = async () =>
    (await this.send<{ count: number }>("POST", "/api/v1/admin/jobs/retry-failed")).count;
  /** De stroom met gebeurtenissen. Via fetch en niet via EventSource, zodat het token in de
   * Authorization-header gaat en niet in het adres; een verlopen token ververst raw() zelf. */
  eventStream = (signal: AbortSignal) =>
    this.raw("/api/v1/events", { headers: { Accept: "text/event-stream" }, signal });
}

// --- J: typen voor previews en achtergrondtaken ---
export interface PreviewMeta {
  /** Afbeelding of video, rechtop. */
  width?: number;
  height?: number;
  /** Video en audio, in seconden. */
  duration?: number;
  pages?: number;
  title?: string;
  artist?: string;
  album?: string;
  /** Tekst: de eerste regels (hooguit 50 regels of 4 KB). */
  text?: string;
  /** Er is meer dan text of entries laat zien. */
  truncated?: boolean;
  /** Archief: de eerste 200 bestanden; count is het totaal (zip). */
  entries?: { name: string; size: number }[];
  count?: number;
  sizes?: number[];
}

export interface Preview {
  state: "pending" | "ready" | "none" | "failed";
  /** Ondertekende adressen voor een <img>; null als er (nog) geen afbeelding is. */
  thumb: string | null;
  large: string | null;
  meta: PreviewMeta;
}

export interface FailedJob {
  id: string;
  type: string;
  key: string | null;
  attempts: number;
  error: string | null;
  finished_at: string | null;
}

export interface JobsOverview {
  counts: { queued: number; running: number; done: number; failed: number };
  failed: FailedJob[];
}

export const api = new Api();

/** Compat: fmt.bytes (in de taal van de app). */
export function formatBytes(bytes: number): string {
  return fmt.bytes(bytes);
}

/** Compat: fmt.dateTime (in de taal van de app). */
export function formatDate(iso: string): string {
  return fmt.dateTime(iso);
}
