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

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Fetch = typeof fetch;

export class Api {
  private token: string | null = null;
  onLoggedOut: () => void = () => {};

  constructor(private fetchFn: Fetch = (...args) => fetch(...args)) {}

  get loggedIn(): boolean {
    return this.token !== null;
  }

  private async raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
    const headers = new Headers(init.headers);
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    const res = await this.fetchFn(path, { ...init, headers, credentials: "same-origin" });
    if (res.status === 401 && retry && this.token && (await this.refresh())) {
      return this.raw(path, init, false);
    }
    if (!res.ok) {
      let detail = `Fout ${res.status}`;
      try {
        const body = await res.json();
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        /* geen JSON */
      }
      if (res.status === 401) {
        this.token = null;
        this.onLoggedOut();
      }
      throw new ApiError(res.status, detail);
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

  /** Probeert in te loggen met het refresh-cookie; geeft de gebruiker of null. */
  async refresh(): Promise<User | null> {
    const res = await this.fetchFn("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
    });
    if (!res.ok) {
      this.token = null;
      return null;
    }
    return this.accept((await res.json()) as Tokens);
  }

  setupRequired = async () =>
    (await this.json<{ setup_required: boolean }>("/api/v1/auth/status")).setup_required;
  setup = async (email: string, display_name: string, password: string) =>
    this.accept(await this.send<Tokens>("POST", "/api/v1/auth/setup", { email, display_name, password }));
  login = async (email: string, password: string) =>
    this.accept(await this.send<Tokens>("POST", "/api/v1/auth/login", { email, password }));
  async logout(): Promise<void> {
    await this.send("POST", "/api/v1/auth/logout").catch(() => {});
    this.token = null;
  }
  me = () => this.json<User>("/api/v1/auth/me");

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

  /** Uploadt één bestand met voortgang (XMLHttpRequest, want fetch meldt geen uploadvoortgang). */
  upload(parent: string, file: File, onProgress: (fraction: number) => void): Promise<FileNode> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", `/api/v1/files/${parent}/upload?name=${encodeURIComponent(file.name)}`);
      if (this.token) xhr.setRequestHeader("Authorization", `Bearer ${this.token}`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
        else {
          let detail = `Fout ${xhr.status}`;
          try {
            detail = JSON.parse(xhr.responseText).detail ?? detail;
          } catch {
            /* geen JSON */
          }
          reject(new ApiError(xhr.status, detail));
        }
      };
      xhr.onerror = () => reject(new ApiError(0, "Netwerkfout"));
      xhr.send(file);
    });
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
