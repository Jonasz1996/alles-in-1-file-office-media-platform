// API-client. Het access-token staat alleen in het geheugen; het refresh-token zit in een
// httpOnly-cookie, zodat JavaScript het niet kan lezen. Bij een 401 wordt één keer ververst.

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
  kind: "file" | "dir";
  mime: string | null;
  size: number;
  etag: string;
  created_at: string;
  modified_at: string;
  deleted_at: string | null;
}

export interface Listing {
  folder: FileNode;
  path: FileNode[];
  children: FileNode[];
}

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
  ) {
    super(message);
  }
}

/** Leest {"detail": "..."} of {"detail": {"message": "...", "code": "..."}} uit een fout. */
export function errorFromBody(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return new ApiError(status, detail);
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: string; code?: string };
    return new ApiError(status, d.message, d.code);
  }
  if (Array.isArray(detail) && detail[0]?.msg) return new ApiError(status, String(detail[0].msg));
  return new ApiError(status, `Fout ${status}`);
}

const TUS = { "Tus-Resumable": "1.0.0" };

/** tus Upload-Metadata: sleutel en base64 van de UTF-8-waarde, gescheiden door komma's. */
export function tusMetadata(values: Record<string, string>): string {
  return Object.entries(values)
    .map(([k, v]) => `${k} ${btoa(String.fromCharCode(...new TextEncoder().encode(v)))}`)
    .join(",");
}

type Fetch = typeof fetch;

export class Api {
  private token: string | null = null;
  private refreshing: Promise<User | null> | null = null;
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

  private async raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
    const headers = new Headers(init.headers);
    const hadToken = this.token !== null;
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    const res = await this.fetchFn(path, { ...init, headers, credentials: "same-origin" });
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
    return this.json<T>(path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
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
    const res = await this.fetchFn("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
    });
    if (!res.ok) {
      this.token = null;
      // Er was een sessie en die is nu voorbij (bijv. uitgelogd in een ander tabblad). Ook
      // melden als dit niet via een gewoon verzoek liep (de upload ververst zelf), want
      // zonder token komt er daarna geen 401 meer die dat nog doet.
      if (wasLoggedIn) this.onLoggedOut();
      return null;
    }
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
   * bestand is het meteen klaar en komt nodeId terug. */
  async tusCreate(parent: string, name: string, size: number): Promise<{ url: string; nodeId: string | null }> {
    const res = await this.raw("/api/v1/tus", {
      method: "POST",
      headers: { ...TUS, "Upload-Length": String(size), "Upload-Metadata": tusMetadata({ filename: name, parent }) },
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
      if (e instanceof ApiError && e.status === 413) throw new ApiError(413, "Onvoldoende quotum");
      throw e;
    }
    return { offset: Number(res.headers.get("Upload-Offset") ?? 0), nodeId: res.headers.get("Platform-Node-Id") };
  }
  async tusCancel(url: string): Promise<void> {
    await this.raw(url, { method: "DELETE", headers: TUS });
  }
}

export const api = new Api();

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(i === 0 ? 0 : 1).replace(".", ",")} ${units[i]}`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("nl-NL", { dateStyle: "medium", timeStyle: "short" });
}
