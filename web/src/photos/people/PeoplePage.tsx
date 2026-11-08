// /fotos/personen (fase 6, van Z6; Keuze 38): je eigen personen als kaarten met omslag en aantal,
// een zoekveld op de naam (Enter opent de eerste) en, als je wilt, de verborgen personen.
// Hernoemen, samenvoegen, verbergen en wissen gebeuren op de pagina van één persoon. Het contract:
// de default export is de pagina van de route (RouteProps), in een eigen chunk. Wat je typt,
// blijft in de pagina (nooit in de adresbalk).
//
// Z0b (review contract F3): de plek van X2, PeopleFaces (de voortgang, "Is dit …?" en "Wie is
// dit?"), lazy met het gebied photos_faces. Z6 houdt hem; src/lazySlots.test.ts eist hem.

import { type JSX, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { useReloadOn } from "../../events";
import { collator, errorText, fold, lazyWithAreas, t } from "../../i18n";
import { Link } from "../../links";
import { navigate, type RouteProps } from "../../router";
import { EmptyState } from "../../ui/EmptyState";
import { Field } from "../../ui/Field";
import { Icon } from "../../ui/Icon";
import { LazySlot } from "../../ui/LazySlot";
import { PageTitle } from "../../ui/PageTitle";
import { Skeleton } from "../../ui/Spinner";
import { people } from "./api";
import { nameMatches } from "./combo";
import { PEOPLE_CHANGED, type Person } from "./types";
import "./people.css";

const PeopleFaces = lazyWithAreas(() => import("../faces/PeopleFaces"), ["photos_faces"]);

/** Hooguit zoveel ids per POST /previews. */
const PREVIEW_BATCH = 500;

/** De thumbnails van de omslagen (node-id → adres), in stukken van 500. */
export async function coverThumbs(list: readonly Person[]): Promise<Record<string, string | null>> {
  const ids = [...new Set(list.map((p) => p.cover).filter((c): c is string => !!c))];
  const out: Record<string, string | null> = {};
  for (let i = 0; i < ids.length; i += PREVIEW_BATCH) {
    const got = await api.previews(ids.slice(i, i + PREVIEW_BATCH));
    for (const [id, p] of Object.entries(got)) out[id] = p.thumb;
  }
  return out;
}

/** Op naam, zoals de server zoekt: een woord van de naam begint met wat je typt. */
export function filterPeople(list: readonly Person[], typed: string): Person[] {
  const q = fold(typed.trim());
  return list.filter((p) => nameMatches(p.name, q)).sort((a, b) => collator.compare(a.name, b.name));
}

function PersonCard({ p, thumb }: { p: Person; thumb: string | null | undefined }): JSX.Element {
  return (
    <Link to={`/fotos/personen/${encodeURIComponent(p.id)}`} className="person-card">
      <span className="person-cover" aria-hidden="true">
        {thumb ? <img src={thumb} alt="" decoding="async" /> : <Icon name="user" size={32} />}
      </span>
      <span className="person-name">{p.name}</span>
      <span className="muted">
        {t("photos_people.count", { n: p.count })}
        {p.hidden && ` · ${t("photos_people.page.hidden")}`}
      </span>
    </Link>
  );
}

export default function PeoplePage(_: RouteProps): JSX.Element {
  const [list, setList] = useState<Person[] | null>(null);
  const [hidden, setHidden] = useState<Person[] | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [typed, setTyped] = useState("");
  const [thumbs, setThumbs] = useState<Record<string, string | null>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [shown, gone] = await Promise.all([people(false), showHidden ? people(true) : null]);
      setList(shown.people);
      setHidden(gone?.people ?? null);
      setError(null);
      setThumbs(await coverThumbs([...shown.people, ...(gone?.people ?? [])]));
    } catch (e) {
      setError(errorText(e));
    }
  }, [showHidden]);

  useEffect(() => {
    void load();
  }, [load]);
  useReloadOn(
    (e) => e.type === PEOPLE_CHANGED,
    () => void load(),
  );

  const found = useMemo(() => (list ? filterPeople(list, typed) : []), [list, typed]);
  const hiddenFound = useMemo(() => (hidden ? filterPeople(hidden, typed) : []), [hidden, typed]);
  const any = !!list && (list.length > 0 || (hidden?.length ?? 0) > 0);

  return (
    <main className="wide people-page">
      <PageTitle>{t("photos_people.title")}</PageTitle>
      <LazySlot>
        <PeopleFaces />
      </LazySlot>
      {error && <p role="alert">{error}</p>}
      {!list && !error && <Skeleton />}
      {list && !any && <EmptyState icon="user" title={t("photos_people.page.empty")} />}
      {any && (
        <div className="people-search">
          <Field label={t("photos_people.page.search")} hint={t("photos_people.page.searchHint")}>
            {(c) => (
              <input
                {...c}
                type="search"
                autoComplete="off"
                spellCheck={false}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  const first = found[0] ?? hiddenFound[0];
                  if (!first) return;
                  e.preventDefault();
                  navigate(`/fotos/personen/${encodeURIComponent(first.id)}`);
                }}
              />
            )}
          </Field>
        </div>
      )}
      {list && list.length > 0 && (
        <section aria-labelledby="personen-lijst">
          <h2 id="personen-lijst" className="sr-only">
            {t("photos_people.page.list")}
          </h2>
          {found.length === 0 ? (
            <p className="muted" role="status">
              {t("photos_people.page.none")}
            </p>
          ) : (
            <ul className="people-cards">
              {found.map((p) => (
                <li key={p.id}>
                  <PersonCard p={p} thumb={thumbs[p.cover ?? ""]} />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {list && (
        <p>
          <label className="check">
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
            {t("photos_people.page.showHidden")}
          </label>
        </p>
      )}
      {showHidden && hidden && (
        <section aria-labelledby="personen-verborgen">
          <h2 id="personen-verborgen">{t("photos_people.page.hiddenTitle")}</h2>
          {hiddenFound.length === 0 ? (
            <p className="muted">{t("photos_people.page.hiddenNone")}</p>
          ) : (
            <ul className="people-cards">
              {hiddenFound.map((p) => (
                <li key={p.id}>
                  <PersonCard p={p} thumb={thumbs[p.cover ?? ""]} />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </main>
  );
}
