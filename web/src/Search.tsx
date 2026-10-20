import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, type Category, formatBytes, formatDate, type SearchQuery, type SearchResult, type Tag } from "./api";
import { navigate } from "./router";
import {
  CATEGORY_LABELS,
  folderHref,
  hasFilter,
  MAX_LIMIT,
  parentHref,
  pathLabel,
  readSearch,
  searchHref,
} from "./searchQuery";
import { TagChips } from "./Tags";
import "./files.css";

/** Zoekveld boven in de bestandenlijst; de resultaten staan op /zoeken. */
export function SearchBox() {
  const [q, setQ] = useState("");
  return (
    <form
      role="search"
      className="search-box"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) navigate(searchHref({ q }));
      }}
    >
      <input
        type="search"
        aria-label="Zoeken in je bestanden"
        placeholder="Zoeken in je bestanden"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <button type="submit">Zoeken</button>
    </form>
  );
}

/** Naar een andere zoekopdracht. navigate() kijkt alleen naar het pad, en dat blijft /zoeken. */
function goTo(href: string) {
  if (href === window.location.pathname + window.location.search) return;
  window.history.pushState(null, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
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

export function Search() {
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
                        {pathLabel(hit.path)}
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
