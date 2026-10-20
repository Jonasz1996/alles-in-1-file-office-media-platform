import { useEffect, useState } from "react";

/** Minimale router op basis van de History-API: geen externe bibliotheek nodig. */
export function navigate(path: string): void {
  if (path !== window.location.pathname) {
    window.history.pushState(null, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }
}

export function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => setPath(window.location.pathname);
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return path;
}
