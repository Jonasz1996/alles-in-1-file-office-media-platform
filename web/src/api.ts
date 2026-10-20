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
