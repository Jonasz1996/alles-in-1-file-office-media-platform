// /kijken/:node (fase 5, van M6). De afspeelpagina; ?t= is de start in seconden. Achter de poort
// van M0 (media/Gate.tsx): staat media uit, dan "niet gevonden". Het contract: de default export
// is de pagina van de route (RouteProps).
//
// De titel, de hoes en wat er hierna komt (de volgende aflevering, het volgende deel) komen uit
// de bibliotheek (getItem en getShow, M2). Lukt dat niet (het bestand staat in geen bibliotheek:
// 404), dan de naam van het bestand en geen kaart: afspelen kan altijd. De maat van het item (voor
// decodingInfo) geeft het Item van M2 niet; hier meet de speler dus op 1080p (m6.md, afwijking 5).

import { type JSX, useEffect, useState } from "react";
import { api, type FileNode } from "../api";
import { t } from "../i18n";
import { itemPath, MEDIA_HOME, watchPath } from "../media";
import { getItem, getShow } from "../media/api";
import { MediaGate } from "../media/Gate";
import { back, navigate, type RouteProps, useLocation } from "../router";
import { episodeTag, nextEpisode, nextPart, prevEpisode } from "./next";
import Player, { type NextUp } from "./ui/Player";

/** ?t= als seconden (≥ 0), anders 0. */
export function startFrom(value: string | null): number {
  if (value === null || !/^\d{1,6}(\.\d+)?$/.test(value.trim())) return 0;
  return Number(value);
}

interface Meta {
  title: string;
  artist: string | null;
  poster: string | null;
  next: NextUp | null;
  prev: string | null;
  library: boolean;
}

async function loadMeta(nodeId: string): Promise<Meta> {
  try {
    const item = await getItem(nodeId);
    const meta: Meta = { title: item.title, artist: null, poster: item.poster, next: null, prev: null, library: true };
    const part = nextPart(item, nodeId);
    if (part) meta.next = { nodeId: part, title: item.title, part: true };
    if (item.show_id) {
      try {
        const show = await getShow(item.show_id);
        meta.artist = show.show.title;
        const n = nextEpisode(show, nodeId);
        if (n?.node_id && !meta.next) meta.next = { nodeId: n.node_id, title: n.title, tag: episodeTag(n), part: false };
        meta.prev = prevEpisode(show, nodeId)?.node_id ?? null;
      } catch {
        // zonder serie: geen kaart
      }
    }
    return meta;
  } catch {
    // Geen item in een bibliotheek: de naam van het bestand
    const node = await api.request<FileNode>("GET", `/api/v1/files/${encodeURIComponent(nodeId)}`).catch(() => null);
    return { title: node?.name ?? t("player.title"), artist: null, poster: null, next: null, prev: null, library: false };
  }
}

function Watch({ nodeId }: { nodeId: string }): JSX.Element {
  const { query } = useLocation();
  const [startS] = useState(() => startFrom(query.get("t")));
  const [meta, setMeta] = useState<Meta | null>(null);

  useEffect(() => {
    let live = true;
    void loadMeta(nodeId).then((m) => live && setMeta(m));
    return () => {
      live = false;
    };
  }, [nodeId]);

  const title = meta?.title ?? t("player.title");
  useEffect(() => {
    document.title = t("shell.pageTitle", { title });
  }, [title]);

  return (
    <main className="watch">
      <Player
        nodeId={nodeId}
        where="watch"
        title={title}
        artist={meta?.artist}
        poster={meta?.poster}
        startS={startS}
        next={meta?.next}
        prev={meta?.prev}
        onGo={(id) => navigate(watchPath(id), { replace: true })}
        onBack={() => back(meta?.library ? itemPath(nodeId) : MEDIA_HOME)}
      />
    </main>
  );
}

export default function WatchPage({ params }: RouteProps): JSX.Element {
  // Een eigen speler per item: na "volgende aflevering" begint alles opnieuw
  return <MediaGate params={params}>{() => <Watch key={params.node} nodeId={params.node} />}</MediaGate>;
}
