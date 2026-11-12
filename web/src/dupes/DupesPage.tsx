// /dubbel (fase 6, van Z5; Keuze 34–36): dubbele bestanden en foto's die erop lijken, in groepen,
// met "Niet gelijk" en "Naar de prullenbak" voor wat de gebruiker zelf aanvinkt. Er staat nooit
// iets vooraf aangevinkt. Opruimen gaat via de bestaande batchroute (crossApi.trash), met
// Ongedaan maken. De default export is de pagina van de route (RouteProps), in een eigen chunk.

import { type JSX, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, type FileNode } from "../api";
import { usePreviewRecord } from "../browser/previews";
import { crossApi } from "../crossApi";
import { errorText, t } from "../i18n";
import { type RouteProps, useQueryParam } from "../router";
import { Button } from "../ui/Button";
import { confirm } from "../ui/dialogs";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";
import { Spinner } from "../ui/Spinner";
import { tabPanelProps, Tabs } from "../ui/Tabs";
import { toast } from "../ui/toast";
import { useViewerParam, Viewer } from "../viewer";
import { duplicates, ignoreDuplicates } from "./api";
import { Group, groupHeadingId } from "./Group";
import {
  allPicked,
  appendGroups,
  dropGroup,
  dropItems,
  focusAfter,
  kindOf,
  NO_TICKS,
  paramOf,
  pickedIds,
  pruneTicks,
  tick,
  tickedIn,
  type Ticks,
} from "./model";
import type { DupeGroup, DupeKind } from "./types";
import "./dupes.css";

/** ?soort=lijkt in de adresbalk: zo werken terug en herladen. Zonder: precies gelijk. */
const KIND_PARAM = "soort";

interface TabState {
  groups: DupeGroup[];
  cursor: string | null;
  status: "loading" | "ready" | "error";
  error?: string;
  loadingMore: boolean;
}

const LOADING: TabState = { groups: [], cursor: null, status: "loading", loadingMore: false };

export default function DupesPage(_: RouteProps): JSX.Element {
  const [param, setParam] = useQueryParam(KIND_PARAM);
  const kind = kindOf(param);
  const [tabs, setTabs] = useState<Partial<Record<DupeKind, TabState>>>({});
  // Wat aangevinkt is: begint leeg, en alleen de gebruiker vinkt iets aan (Keuze 36)
  const [ticks, setTicks] = useState<Record<DupeKind, Ticks>>({ exact: NO_TICKS, near: NO_TICKS });
  const [busy, setBusy] = useState<string | null>(null);
  const [viewing, setViewing] = useViewerParam();
  const gen = useRef<Record<DupeKind, number>>({ exact: 0, near: 0 });
  const latest = useRef(tabs);
  latest.current = tabs;
  // Na een actie: de kop van deze groep krijgt de focus (key null: de kop van de pagina)
  const focusWanted = useRef<{ key: string | null } | null>(null);
  const moreButton = useRef<HTMLButtonElement>(null);

  const state = tabs[kind] ?? LOADING;

  const load = useCallback((k: DupeKind) => {
    const mine = ++gen.current[k];
    setTabs((s) => ({ ...s, [k]: LOADING }));
    duplicates(k).then(
      (page) => {
        if (gen.current[k] !== mine) return;
        const groups = appendGroups([], page.groups);
        setTabs((s) => ({ ...s, [k]: { groups, cursor: page.cursor, status: "ready", loadingMore: false } }));
        // Wat aangevinkt was en nog bestaat, blijft aangevinkt; de rest valt weg
        setTicks((tk) => ({ ...tk, [k]: pruneTicks(tk[k], groups) }));
      },
      (e: unknown) => {
        if (gen.current[k] !== mine) return;
        setTabs((s) => ({ ...s, [k]: { ...LOADING, status: "error", error: errorText(e) } }));
      },
    );
  }, []);

  useEffect(() => {
    if (!latest.current[kind]) load(kind);
  }, [kind, load]);

  function setGroups(k: DupeKind, groups: DupeGroup[]) {
    setTabs((s) => {
      const cur = s[k];
      return cur ? { ...s, [k]: { ...cur, groups } } : s;
    });
    setTicks((tk) => ({ ...tk, [k]: pruneTicks(tk[k], groups) }));
  }

  async function loadMore() {
    const k = kind;
    const cur = latest.current[k];
    if (!cur || cur.cursor === null || cur.loadingMore) return;
    const mine = gen.current[k];
    setTabs((s) => ({ ...s, [k]: { ...cur, loadingMore: true } }));
    try {
      const page = await duplicates(k, cur.cursor);
      if (gen.current[k] !== mine) return;
      const have = latest.current[k]?.groups ?? cur.groups;
      const groups = appendGroups(have, page.groups);
      // Is de knop weg (alles geladen), dan de eerste nieuwe groep; anders blijft de focus op de knop
      const first = groups[have.length];
      if (page.cursor === null && first && moreButton.current?.contains(document.activeElement))
        focusWanted.current = { key: first.key };
      setTabs((s) => ({ ...s, [k]: { groups, cursor: page.cursor, status: "ready", loadingMore: false } }));
    } catch (e) {
      if (gen.current[k] !== mine) return;
      setTabs((s) => ({ ...s, [k]: { ...(s[k] ?? cur), loadingMore: false } }));
      toast(errorText(e), { kind: "error" });
    }
  }

  async function trash(group: DupeGroup) {
    const k = kind;
    const ids = pickedIds(group, ticks[k]);
    if (ids.length === 0) return;
    if (allPicked(group, ticks[k])) {
      const ok = await confirm({
        title: t("dupes.trash.allTitle"),
        message: t("dupes.trash.allMessage", { n: ids.length }),
        confirm: t("dupes.trash.allConfirm", { n: ids.length }),
        danger: true,
      });
      if (!ok) return;
    }
    setBusy(group.key);
    let done: string[] = [];
    try {
      const r = await crossApi.trash(ids);
      done = r.results.filter((x) => x.ok).map((x) => x.id);
      const failed = r.results.find((x) => !x.ok);
      if (failed) toast(errorText(failed), { kind: "error" });
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
    setBusy(null);
    if (done.length === 0) return;
    const before = latest.current[k]?.groups ?? [];
    const after = dropItems(before, new Set(done));
    focusWanted.current = { key: focusAfter(before, after, group.key) };
    setGroups(k, after);
    toast(t("dupes.trash.done", { n: done.length }), {
      kind: "ok",
      action: {
        label: t("dupes.trash.undo"),
        run: () =>
          void crossApi.restore(done).then(
            () => load(k),
            (e: unknown) => toast(errorText(e), { kind: "error" }),
          ),
      },
    });
  }

  async function notSame(group: DupeGroup) {
    const k = kind;
    setBusy(group.key);
    try {
      await ignoreDuplicates(group.key);
    } catch (e) {
      setBusy(null);
      toast(errorText(e), { kind: "error" });
      // De groep bestaat niet meer (een bestand is intussen weg of veranderd): opnieuw laden
      if (e instanceof ApiError && e.code === "dupes_bad_key") load(k);
      return;
    }
    setBusy(null);
    const before = latest.current[k]?.groups ?? [];
    const after = dropGroup(before, group.key);
    focusWanted.current = { key: focusAfter(before, after, group.key) };
    setGroups(k, after);
    toast(t("dupes.ignored"), { kind: "ok" });
  }

  // De focus na een actie: op de kop van de groep (die zegt het niveau en hoeveel er nog zijn)
  useEffect(() => {
    const want = focusWanted.current;
    if (!want) return;
    focusWanted.current = null;
    const el =
      want.key !== null
        ? document.getElementById(groupHeadingId(want.key))
        : document.querySelector<HTMLElement>("h1[data-page-title]");
    el?.focus();
  });

  const nodes = useMemo(() => state.groups.flatMap((g) => g.items.map((it) => it.node)), [state.groups]);
  const previews = usePreviewRecord(nodes);

  // De viewer: de bestanden van één groep, groot (← en → wisselen)
  const viewGroup = viewing ? state.groups.find((g) => g.items.some((it) => it.node.id === viewing)) : undefined;
  const viewItems: FileNode[] = viewGroup ? viewGroup.items.map((it) => it.node) : [];
  const viewIndex = viewItems.findIndex((n) => n.id === viewing);

  return (
    <main className="dupes-page">
      <PageTitle>{t("dupes.title")}</PageTitle>
      <p className="dupes-intro">{t("dupes.intro")}</p>
      <Tabs
        label={t("dupes.tabs.label")}
        idPrefix="dubbel"
        current={kind}
        tabs={[
          { id: "exact", label: t("dupes.tabs.exact") },
          { id: "near", label: t("dupes.tabs.near") },
        ]}
        onSelect={(id) => setParam(paramOf(id === "near" ? "near" : "exact"), { replace: true })}
      />
      <div {...tabPanelProps("dubbel", kind)} className="dupes-panel">
        {kind === "exact" && <p className="muted">{t("dupes.exactNote")}</p>}
        {state.status === "loading" && (
          <p className="muted" role="status">
            <Spinner inline /> {t("dupes.loading")}
          </p>
        )}
        {state.status === "error" && (
          <div role="alert" className="dupes-error">
            <p>
              {t("dupes.failed")} {state.error}
            </p>
            <Button onClick={() => load(kind)}>{t("common.retry")}</Button>
          </div>
        )}
        {state.status === "ready" && state.groups.length === 0 && (
          <EmptyState icon="copy" title={kind === "exact" ? t("dupes.empty.exact") : t("dupes.empty.near")}>
            {kind === "near" ? t("dupes.empty.nearHint") : null}
          </EmptyState>
        )}
        {state.groups.length > 0 && (
          <div className="dupe-list">
            {state.groups.map((g) => (
              <Group
                key={g.key}
                group={g}
                picked={tickedIn(ticks[kind], g.key)}
                previews={previews}
                busy={busy === g.key}
                onPick={(id, on) => setTicks((tk) => ({ ...tk, [kind]: tick(tk[kind], g.key, id, on) }))}
                onTrash={() => void trash(g)}
                onNotSame={() => void notSame(g)}
                onView={setViewing}
              />
            ))}
          </div>
        )}
        {state.status === "ready" && state.cursor !== null && (
          <p>
            <Button ref={moreButton} onClick={() => void loadMore()} busy={state.loadingMore}>
              {t("dupes.more")}
            </Button>
          </p>
        )}
      </div>
      {viewIndex >= 0 && (
        <Suspense fallback={null}>
          <Viewer
            items={viewItems}
            index={viewIndex}
            onIndexChange={(i) => setViewing(viewItems[i].id)}
            onClose={() => setViewing(null)}
          />
        </Suspense>
      )}
    </main>
  );
}
