import { useEffect, useState } from "react";
import { type ApiInfo, fetchInfo } from "./api";

export function App() {
  const [info, setInfo] = useState<ApiInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchInfo()
      .then(setInfo)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main>
      <h1>Platform</h1>
      <p>Bestanden, office en media op één plek.</p>
      <section aria-live="polite">
        {info && (
          <p>
            API {info.version} op node <strong>{info.node}</strong> ({info.environment})
          </p>
        )}
        {error && <p role="alert">API niet bereikbaar: {error}</p>}
        {!info && !error && <p>API controleren…</p>}
      </section>
    </main>
  );
}
