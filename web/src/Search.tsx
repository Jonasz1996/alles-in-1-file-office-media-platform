import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, type Category, formatBytes, formatDate, type SearchQuery, type SearchResult, type Tag } from "./api";
import { isGuest, useCurrentUser } from "./currentUser";
import { navigate, type RouteProps } from "./router";
import {
  CATEGORY_LABELS,
  folderHref,
  hasFilter,
  hitPath,
  MAX_LIMIT,
  parentHref,
  pathLabel,
  readSearch,
  searchHref,
} from "./searchQuery";
import { isShared } from "./sharing";
import { TagChips } from "./Tags";
import "./files.css";
import "./share.css";

/** Zoekveld boven in de bestandenlijst; de resultaten staan op /zoeken. */
export function SearchBox() {
  const [q, setQ] = useState("");
  // Gasten hebben geen eigen bestanden; zoeken gaat dan door wat met hen gedeeld is
  const label = isGuest(useCurrentUser()) ? "Zoeken in wat met je gedeeld is" : "Zoeken in je bestanden";
  return (
    <form
      role="search"
      className="search-box"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) navigate(searchHref({ q }));
      }}
    >
      <input type="search" aria-label={label} placeholder={label} value={q} onChange={(e) => setQ(e.target.value)} />
      <button type="submit">Zoeken</button>
    </form>
  );
}

/** Naar een andere zoekopdracht (navigate vergelijkt ook de query: dezelfde doet niets). */
function goTo(href: string) {
  navigate(href);
}

/** De zoekopdracht uit de adresbalk; volgt terug en vooruit. */
function useSearchQuery(): SearchQuery {
  const [query, setQuery] = useState(() => readSearch(window.location.search));
  useEffect(() => {
    const update = () => setQuery(readSearch(window.location.search));
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return query;
}

export default function Search(_: RouteProps) {
  const query = useSearchQuery();
  const [text, setText] = useState(query.q ?? "");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [tags, setTags] = useState<Tag[]>([]);
  const [error, setError] = useState<string | null>(null);
  const searches = useRef(0);

  useEffect(() => {
    setText(query.q ?? "");
    const seq = ++searches.current;
    if (!hasFilter(query)) {
      setResult(null);
      return;
    }
    api
      .search(query)
      .then((r) => {
        if (seq !== searches.current) return; // intussen een nieuwere zoekopdracht
        setResult(r);
        setError(null);
      })
      .catch((e: Error) => {
        if (seq === searches.current) setError(e.message);
      });
  }, [query]);

  useEffect(() => {
    api
      .tags()
      .then(setTags)
      .catch(() => {});
  }, []);

  function go(change: Partial<SearchQuery>) {
    // Een nieuwe zoekopdracht begint weer bij de eerste resultaten
    goTo(searchHref({ ...query, limit: undefined, ...change }));
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    go({ q: text });
  }

  const limit = query.limit ?? 50;

  return (
    <main className="wide">
      <h1>Zoeken</h1>
      <form className="search-form" role="search" onSubmit={submit}>
        <input
          type="search"
          aria-label="Zoekwoorden"
          placeholder="Naam of een deel ervan"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <select
          aria-label="Soort"
          value={query.type ?? ""}
          onChange={(e) => go({ q: text, type: e.target.value as Category | "" })}
        >
          <option value="">Alle soorten</option>
          {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select aria-label="Tag" value={query.tag ?? ""} onChange={(e) => go({ q: text, tag: e.target.value })}>
          <option value="">Alle tags</option>
          {tags.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({t.count})
            </option>
          ))}
        </select>
        <button type="submit">Zoeken</button>
      </form>

      {error && <p role="alert">{error}</p>}
      {!hasFilter(query) && <p className="muted">Typ een (deel van een) naam, of kies een soort of tag.</p>}
      {result && result.results.length === 0 && <p className="muted">Niets gevonden.</p>}
      {result && result.results.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Map</th>
                <th>Grootte</th>
                <th>Gewijzigd</th>
              </tr>
            </thead>
            <tbody>
              {result.results.map((hit) => {
                const n = hit.node;
                const parent = parentHref(hit);
                // Van iemand anders: het pad begint bij de gedeelde map
                const shared = isShared(n);
                return (
                  <tr key={n.id}>
                    <td>
                      <button
                        type="button"
                        className="link"
                        title={n.kind === "dir" ? "Map openen" : "Map met dit bestand openen"}
                        onClick={() => navigate(folderHref(hit))}
                      >
                        {n.kind === "dir" ? "📁 " : "📄 "}
                        {n.name}
                      </button>
                      {shared && n.owner && <span className="owner-note">van {n.owner.display_name}</span>}
                      <TagChips tags={n.tags} />
                    </td>
                    <td>
                      <a
                        href={parent}
                        onClick={(e) => {
                          e.preventDefault();
                          navigate(parent);
                        }}
                      >
                        {pathLabel(hitPath(hit), shared)}
                      </a>
                    </td>
                    <td>{n.kind === "file" ? formatBytes(n.size) : ""}</td>
                    <td>{formatDate(n.modified_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {result?.more &&
        (limit < MAX_LIMIT ? (
          <p>
            <button type="button" onClick={() => goTo(searchHref({ ...query, limit: MAX_LIMIT }))}>
              Meer tonen
            </button>
          </p>
        ) : (
          <p className="muted">Er zijn nog meer resultaten; maak je zoekopdracht specifieker.</p>
        ))}
    </main>
  );
}
