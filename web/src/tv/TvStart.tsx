// /tv (fase 11, stap 9): het begin van de app voor de LG webOS-tv. tv.ts zette de tv-stand al
// aan (of met ?uit=1 weer uit); deze pagina gaat meteen door naar media.
import { type JSX, useEffect } from "react";
import { navigate, type RouteProps } from "../router";

export default function TvStart(_: RouteProps): JSX.Element {
  useEffect(() => {
    const off = new URLSearchParams(location.search).has("uit");
    navigate(off ? "/" : "/media", { replace: true });
  }, []);
  return <main />;
}
