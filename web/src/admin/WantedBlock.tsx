// Films en series volgen, en de kalender (fase 11, stap 2e). Op /beheer → Media, want volgen is
// voorlopig alleen voor beheerders; verzoeken van gezinsleden komen in stap 6. De API staat in
// routers/arr_wanted.py. Volgen zoekt nog niets: dat doet stap 3.
import {
  type FormEvent,
  type JSX,
  useCallback,
  useEffect,
  useState,
} from "react";
import { api } from "../api";
import { errorText, fmt, t } from "../i18n";
import type { Library } from "../media/types";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { toast } from "../ui/toast";

const BASE = "/api/v1/arr";

type Kind = "movie" | "show";

interface Candidate {
  tmdb_id: number;
  kind: Kind;
  title: string;
  year: number | null;
  overview: string | null;
}

interface Wanted {
  id: string;
  kind: Kind;
  tmdb_id: number;
  title: string;
  year: number | null;
  library_id: string;
  profile: string;
  seasons: number[] | null;
  monitored: boolean;
}

interface Profile {
  key: string;
  name: string;
}

interface CalendarEntry {
  wanted_id: string;
  title: string;
  season: number;
  episode: number;
  episode_title: string | null;
  aired: string;
}

export default function WantedBlock({
  libraries,
}: {
  libraries: Library[];
}): JSX.Element {
  const [kind, setKind] = useState<Kind>("movie");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Candidate[] | null>(null);
  const [wanted, setWanted] = useState<Wanted[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profile, setProfile] = useState("hd-1080p");
  const [calendar, setCalendar] = useState<CalendarEntry[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fitting = libraries.filter(
    (l) => l.kind === (kind === "movie" ? "movies" : "shows"),
  );
  const [libraryId, setLibraryId] = useState("");
  const target =
    fitting.find((l) => l.id === libraryId)?.id ?? fitting[0]?.id ?? "";

  const reload = useCallback(async () => {
    setWanted(await api.request<Wanted[]>("GET", `${BASE}/wanted`));
  }, []);

  useEffect(() => {
    Promise.all([
      reload(),
      api.request<Profile[]>("GET", `${BASE}/profiles`).then(setProfiles),
    ]).catch((e) => setError(errorText(e)));
  }, [reload]);

  async function run(
    key: string,
    fn: () => Promise<string | null>,
  ): Promise<void> {
    setBusy(key);
    try {
      const message = await fn();
      setError(null);
      if (message) toast(message, { kind: "ok" });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  function search(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    void run("search", async () => {
      const params = new URLSearchParams({ kind, q });
      setFound(
        await api.request<Candidate[]>("GET", `${BASE}/lookup?${params}`),
      );
      return null;
    });
  }

  function follow(c: Candidate) {
    void run(`add-${c.tmdb_id}`, async () => {
      await api.request<Wanted>("POST", `${BASE}/wanted`, {
        kind: c.kind,
        tmdb_id: c.tmdb_id,
        library_id: target,
        profile,
      });
      await reload();
      return t("admin.media.wanted.added", { title: c.title });
    });
  }

  function unfollow(w: Wanted) {
    void run(`remove-${w.id}`, async () => {
      await api.request<void>(
        "DELETE",
        `${BASE}/wanted/${encodeURIComponent(w.id)}`,
      );
      await reload();
      return t("admin.media.wanted.removed", { title: w.title });
    });
  }

  const libraryName = (id: string) =>
    libraries.find((l) => l.id === id)?.name ?? "?";
  const profileName = (key: string) =>
    profiles.find((p) => p.key === key)?.name ?? key;

  return (
    <section aria-labelledby="admin-media-wanted">
      <div className="admin-head">
        <h3 id="admin-media-wanted">{t("admin.media.wanted.title")}</h3>
      </div>
      <p className="muted">{t("admin.media.wanted.intro")}</p>

      <form className="stack" onSubmit={search} data-arr="lookup">
        <div className="toolbar">
          <label>
            <input
              type="radio"
              name="wanted-kind"
              checked={kind === "movie"}
              onChange={() => setKind("movie")}
            />{" "}
            {t("admin.media.wanted.movie")}
          </label>
          <label>
            <input
              type="radio"
              name="wanted-kind"
              checked={kind === "show"}
              onChange={() => setKind("show")}
            />{" "}
            {t("admin.media.wanted.show")}
          </label>
        </div>
        <Field label={t("admin.media.wanted.search")}>
          {(c) => (
            <input
              {...c}
              type="search"
              name="wanted-q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("admin.media.wanted.library")}>
          {(c) => (
            <select
              {...c}
              name="wanted-library"
              value={target}
              onChange={(e) => setLibraryId(e.target.value)}
            >
              {fitting.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("admin.media.wanted.profile")}>
          {(c) => (
            <select
              {...c}
              name="wanted-profile"
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
            >
              {profiles.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        {fitting.length === 0 && (
          <p className="muted">{t("admin.media.wanted.noLibrary")}</p>
        )}
        <div className="toolbar">
          <Button
            type="submit"
            variant="primary"
            busy={busy === "search"}
            disabled={!query.trim()}
          >
            {t("admin.media.wanted.searchButton")}
          </Button>
        </div>
      </form>

      {found !== null &&
        (found.length === 0 ? (
          <p className="muted">{t("admin.media.wanted.nothing")}</p>
        ) : (
          <ul className="stack" data-arr="found">
            {found.map((c) => (
              <li key={c.tmdb_id}>
                <strong>{c.title}</strong> {c.year ? `(${c.year})` : ""}{" "}
                <Button
                  busy={busy === `add-${c.tmdb_id}`}
                  disabled={!target}
                  onClick={() => follow(c)}
                >
                  {t("admin.media.wanted.follow")}
                </Button>
                {c.overview && <p className="muted">{c.overview}</p>}
              </li>
            ))}
          </ul>
        ))}

      <h4>{t("admin.media.wanted.list", { n: wanted.length })}</h4>
      {wanted.length > 0 && (
        <div className="table-wrap">
          <table data-arr="wanted">
            <thead>
              <tr>
                <th>{t("admin.media.wanted.titleCol")}</th>
                <th className="wide-only">{t("admin.media.wanted.library")}</th>
                <th>{t("admin.media.wanted.profile")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {wanted.map((w) => (
                <tr key={w.id}>
                  <th scope="row">
                    {w.title} {w.year ? `(${w.year})` : ""}
                  </th>
                  <td className="wide-only">{libraryName(w.library_id)}</td>
                  <td>{profileName(w.profile)}</td>
                  <td>
                    <Button
                      variant="danger"
                      busy={busy === `remove-${w.id}`}
                      onClick={() => unfollow(w)}
                    >
                      {t("admin.media.wanted.unfollow")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="toolbar">
        <Button
          busy={busy === "calendar"}
          onClick={() =>
            void run("calendar", async () => {
              setCalendar(
                await api.request<CalendarEntry[]>(
                  "GET",
                  `${BASE}/calendar?days=30`,
                ),
              );
              return null;
            })
          }
        >
          {t("admin.media.wanted.calendar")}
        </Button>
      </div>
      {calendar !== null &&
        (calendar.length === 0 ? (
          <p className="muted">{t("admin.media.wanted.calendarEmpty")}</p>
        ) : (
          <ul data-arr="calendar">
            {calendar.map((e) => (
              <li key={`${e.wanted_id}-${e.season}-${e.episode}`}>
                {t("admin.media.wanted.calendarLine", {
                  date: fmt.date(e.aired),
                  title: e.title,
                  code: `S${String(e.season).padStart(2, "0")}E${String(e.episode).padStart(2, "0")}`,
                })}
                {e.episode_title ? ` · ${e.episode_title}` : ""}
              </li>
            ))}
          </ul>
        ))}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
