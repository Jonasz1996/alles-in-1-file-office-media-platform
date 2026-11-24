// /media/verzoeken (fase 11, stap 6): een film of serie vragen. Zoeken bij TMDB, vragen, en zien
// hoe het met je verzoeken staat. Een beheerder ziet hier ook de verzoeken die wachten en keurt
// ze goed of wijst ze af. De API staat in api/app/routers/arr_requests.py; een live melding
// (arr.request) laadt de lijst opnieuw en zegt het aan wie het vroeg.
import { type FormEvent, type JSX, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { isGuest, useCurrentUser } from "../currentUser";
import { useServerEvents } from "../events";
import { errorText, fmt, t } from "../i18n";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Field } from "../ui/Field";
import { PageTitle } from "../ui/PageTitle";
import { toast } from "../ui/toast";
import { MediaGate } from "./Gate";
import type { RouteProps } from "../router";
import "./media.css";

const BASE = "/api/v1/arr/requests";

type Kind = "movie" | "show";
type State = "pending" | "approved" | "declined" | "available";

interface Candidate {
  tmdb_id: number;
  kind: Kind;
  title: string;
  year: number | null;
  overview: string | null;
  status: "none" | "requested" | "following";
}

interface Request {
  id: string;
  kind: Kind;
  title: string;
  year: number | null;
  note: string | null;
  state: State;
  reason: string | null;
  user_id: string;
  user_name: string;
  created_at: string;
}

function titleOf(r: { title: string; year: number | null }): string {
  return r.year ? `${r.title} (${r.year})` : r.title;
}

function Requests(): JSX.Element {
  const me = useCurrentUser();
  const admin = me?.role === "admin";
  const [kind, setKind] = useState<Kind>("movie");
  const [query, setQuery] = useState("");
  const [note, setNote] = useState("");
  const [found, setFound] = useState<Candidate[] | null>(null);
  const [mine, setMine] = useState<Request[]>([]);
  const [waiting, setWaiting] = useState<Request[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setMine(await api.request<Request[]>("GET", BASE));
    if (admin) {
      const all = await api.request<Request[]>("GET", `${BASE}?scope=all`);
      setWaiting(all.filter((r) => r.state === "pending"));
    }
  }, [admin]);

  useEffect(() => {
    reload().catch((e) => setError(errorText(e)));
  }, [reload]);

  useServerEvents((e) => {
    if (e.type !== "arr.request") return;
    const title = String(e.data.title ?? "");
    const state = e.data.state;
    if (state === "approved" || state === "declined" || state === "available") {
      toast(t(`media.requests.told.${state}`, { title }), { kind: "ok" });
    }
    void reload().catch(() => {});
  });

  async function run(key: string, fn: () => Promise<string | null>): Promise<void> {
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
      setFound(await api.request<Candidate[]>("GET", `${BASE}/lookup?${new URLSearchParams({ kind, q })}`));
      return null;
    });
  }

  function ask(c: Candidate) {
    void run(`ask-${c.tmdb_id}`, async () => {
      const r = await api.request<Request>("POST", BASE, {
        kind: c.kind,
        tmdb_id: c.tmdb_id,
        note: note.trim() || null,
      });
      setFound((list) => list?.map((x) => (x.tmdb_id === c.tmdb_id ? { ...x, status: "requested" } : x)) ?? null);
      await reload();
      return r.state === "pending"
        ? t("media.requests.asked", { title: r.title })
        : t("media.requests.askedApproved", { title: r.title });
    });
  }

  function decide(r: Request, ok: boolean) {
    void run(`${ok ? "approve" : "decline"}-${r.id}`, async () => {
      await api.request<Request>("POST", `${BASE}/${encodeURIComponent(r.id)}/${ok ? "approve" : "decline"}`, {});
      await reload();
      return ok ? t("media.requests.approvedOne", { title: r.title }) : t("media.requests.declinedOne", { title: r.title });
    });
  }

  function withdraw(r: Request) {
    void run(`withdraw-${r.id}`, async () => {
      await api.request<void>("DELETE", `${BASE}/${encodeURIComponent(r.id)}`);
      await reload();
      return t("media.requests.withdrawn", { title: r.title });
    });
  }

  if (isGuest(me)) {
    return (
      <main className="wide media-page">
        <PageTitle>{t("media.requests.title")}</PageTitle>
        <p>{t("media.requests.guest")}</p>
      </main>
    );
  }

  return (
    <main className="wide media-page" data-arr="requests">
      <PageTitle>{t("media.requests.title")}</PageTitle>
      <p className="muted">{admin ? t("media.requests.introAdmin") : t("media.requests.intro")}</p>

      <form className="stack" onSubmit={search}>
        <div className="toolbar">
          <label>
            <input type="radio" name="request-kind" checked={kind === "movie"} onChange={() => setKind("movie")} />{" "}
            {t("media.kinds.movies")}
          </label>
          <label>
            <input type="radio" name="request-kind" checked={kind === "show"} onChange={() => setKind("show")} />{" "}
            {t("media.kinds.shows")}
          </label>
        </div>
        <Field label={t("media.requests.search")}>
          {(c) => <input {...c} type="search" name="request-q" value={query} onChange={(e) => setQuery(e.target.value)} />}
        </Field>
        <Field label={t("media.requests.note")}>
          {(c) => <input {...c} name="request-note" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        <div className="toolbar">
          <Button type="submit" variant="primary" busy={busy === "search"} disabled={!query.trim()}>
            {t("media.requests.searchButton")}
          </Button>
        </div>
      </form>

      {found !== null &&
        (found.length === 0 ? (
          <p className="muted">{t("media.requests.nothing")}</p>
        ) : (
          <ul className="stack" data-arr="request-found">
            {found.map((c) => (
              <li key={c.tmdb_id}>
                <strong>{titleOf(c)}</strong>{" "}
                {c.status === "none" ? (
                  <Button busy={busy === `ask-${c.tmdb_id}`} onClick={() => ask(c)}>
                    {t("media.requests.ask")}
                  </Button>
                ) : (
                  <span className="muted">
                    {t(c.status === "following" ? "media.requests.status.following" : "media.requests.status.requested")}
                  </span>
                )}
                {c.overview && <p className="muted">{c.overview}</p>}
              </li>
            ))}
          </ul>
        ))}

      {admin && (
        <section aria-labelledby="requests-waiting">
          <h2 id="requests-waiting">{t("media.requests.waiting", { n: waiting.length })}</h2>
          {waiting.length === 0 ? (
            <p className="muted">{t("media.requests.noneWaiting")}</p>
          ) : (
            <ul className="stack" data-arr="requests-waiting">
              {waiting.map((r) => (
                <li key={r.id}>
                  <strong>{titleOf(r)}</strong> · {t("media.requests.by", { name: r.user_name, date: fmt.date(r.created_at) })}
                  {r.note && <p className="muted">“{r.note}”</p>}
                  <div className="toolbar">
                    <Button variant="primary" busy={busy === `approve-${r.id}`} onClick={() => decide(r, true)}>
                      {t("media.requests.approve")}
                    </Button>
                    <Button busy={busy === `decline-${r.id}`} onClick={() => decide(r, false)}>
                      {t("media.requests.decline")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section aria-labelledby="requests-mine">
        <h2 id="requests-mine">{t("media.requests.mine")}</h2>
        {mine.length === 0 ? (
          <EmptyState icon="video" title={t("media.requests.noneMine")} />
        ) : (
          <ul className="stack" data-arr="requests-mine">
            {mine.map((r) => (
              <li key={r.id} data-state={r.state}>
                <strong>{titleOf(r)}</strong> · {t(`media.requests.state.${r.state}`)}
                {r.state === "declined" && r.reason ? ` · ${r.reason}` : ""}
                {r.state === "pending" && (
                  <>
                    {" "}
                    <Button busy={busy === `withdraw-${r.id}`} onClick={() => withdraw(r)}>
                      {t("media.requests.withdraw")}
                    </Button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}

export default function RequestsPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <Requests />}</MediaGate>;
}
