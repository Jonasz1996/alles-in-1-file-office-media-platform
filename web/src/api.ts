export interface ApiInfo {
  name: string;
  version: string;
  environment: string;
  node: string;
}

export async function fetchInfo(fetchFn: typeof fetch = fetch): Promise<ApiInfo> {
  const res = await fetchFn("/api/v1/info");
  if (!res.ok) {
    throw new Error(`API antwoordde met ${res.status}`);
  }
  return (await res.json()) as ApiInfo;
}

export interface NodeStatus {
  name: string;
  ip: string;
  checks: Record<string, boolean>;
  db_role: string | null;
  seaweed_leader: boolean;
  disk: { total_bytes: number; used_bytes: number; free_bytes: number } | null;
}

export interface ClusterStatus {
  status: "ok" | "degraded" | "down";
  nodes: NodeStatus[];
  database: { name: string; role: string; state: string; lag_bytes: number | null }[];
  messages: string[];
}

export class UnauthorizedError extends Error {}

export async function fetchClusterStatus(
  token: string,
  fetchFn: typeof fetch = fetch,
): Promise<ClusterStatus> {
  const res = await fetchFn("/api/v1/admin/status", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    throw new UnauthorizedError("Ongeldig beheertoken");
  }
  if (!res.ok) {
    throw new Error(`API antwoordde met ${res.status}`);
  }
  return (await res.json()) as ClusterStatus;
}

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
