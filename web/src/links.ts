// Links binnen de app: <Link> (zonder herladen, maar met de gewone browserfuncties zoals
// openen in een nieuw tabblad) en het adres van een map, album of bestand.

import { type AnchorHTMLAttributes, createElement, type JSX, type MouseEvent } from "react";
import type { FileNode } from "./api";
import { isPlainLeftClick, navigate, useLocation } from "./router";

export type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: string; replace?: boolean };

/**
 * Een link binnen de app. Een gewone klik navigeert zonder herladen; Ctrl/Cmd/Shift/Alt-klik,
 * middelklik en target="_blank" laat hij aan de browser. aria-current="page" als het pad klopt.
 */
export function Link({ to, replace, onClick, target, ...rest }: LinkProps): JSX.Element {
  const { path } = useLocation();
  const here = path === new URL(to, "http://x").pathname && !to.includes("?");
  return createElement("a", {
    "aria-current": here ? "page" : undefined,
    ...rest,
    href: to,
    target,
    onClick: (e: MouseEvent<HTMLAnchorElement>) => {
      onClick?.(e);
      if (e.defaultPrevented) return;
      if (!isPlainLeftClick(e) || (target && target !== "_self")) return;
      e.preventDefault();
      navigate(to, { replace });
    },
  });
}

/**
 * Waar een map, album of bestand in de app staat: een map → /map/<id>, een album → /album/<id>,
 * een bestand → de map waar het in staat met ?bestand=<id> (de viewer), of /gedeeld?bestand=<id>
 * als de map erboven onbekend is (iets dat los met je gedeeld is).
 */
export function nodeHref(n: Pick<FileNode, "id" | "kind" | "parent_id">): string {
  if (n.kind === "dir") return `/map/${n.id}`;
  if (n.kind === "album") return `/album/${n.id}`;
  const q = new URLSearchParams({ bestand: n.id });
  return n.parent_id ? `/map/${n.parent_id}?${q}` : `/gedeeld?${q}`;
}
