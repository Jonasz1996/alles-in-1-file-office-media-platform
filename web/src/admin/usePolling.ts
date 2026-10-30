// Iets elke zoveel seconden opnieuw ophalen, maar niet terwijl het tabblad verborgen is: dan
// zou een beheerpagina die openstaat de server de hele dag bezighouden. Komt het tabblad terug,
// dan meteen een keer.

import { useCallback, useEffect, useRef, useState } from "react";

export interface Polled<T> {
  data: T | null;
  error: unknown;
  /** Nu opnieuw (bijv. na een wijziging) */
  reload(): Promise<void>;
}

export function usePolling<T>(load: () => Promise<T>, intervalMs: number): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const latest = useRef(load);
  const seq = useRef(0);
  useEffect(() => {
    latest.current = load;
  });

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const value = await latest.current();
      if (mine !== seq.current) return; // een nieuwer verzoek was er eerder
      setData(value);
      setError(null);
    } catch (e) {
      if (mine === seq.current) setError(e);
    }
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null && intervalMs > 0) timer = setInterval(() => void reload(), intervalMs);
    };
    const stop = () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else {
        void reload();
        start();
      }
    };
    void reload();
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, reload]);

  return { data, error, reload };
}
