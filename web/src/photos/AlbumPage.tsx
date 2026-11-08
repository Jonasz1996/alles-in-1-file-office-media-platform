// Eén album (/album/:id): de foto's in hetzelfde raster als de tijdlijn (zonder maandkoppen), de
// viewer, en downloaden als zip. De eigenaar deelt het album (alleen om te bekijken), hernoemt
// het, kiest een omslag, haalt foto's eruit en verwijdert het. Wie het gedeeld kreeg, kijkt en
// downloadt alleen.
//
// Slimme albums (fase 6, Z6; Keuze 42): de eigenaar geeft het album een regel (de knop Regel, een
// eigen chunk met het gebied photos_people) en ziet bij elke foto die alleen hij ziet het merkje
// "alleen voor jou". Deelt hij een album met een regel waarvan nieuwe foto's nog niet gedeeld
// worden (een persoon, een groep of een publieke link erbij), dan volgt de vraag of nieuwe foto's
// ook gedeeld worden (people/shareQuestion.ts).

import { type JSX, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { MAX_BATCH_IDS } from "../crossApi";
import { useServerEvents } from "../events";
import { areasLoaded, errorText, lazyWithAreas, loadAreas, t } from "../i18n";
import { Link } from "../links";
import { navigate, type RouteProps, useNavSection } from "../router";
import { ShareDialog } from "../ShareDialog";
import { Button } from "../ui/Button";
import { confirm, prompt } from "../ui/dialogs";
import { EmptyState } from "../ui/EmptyState";
import { useScope, useShortcuts } from "../ui/keys";
import { PageTitle } from "../ui/PageTitle";
import { LazySlot } from "../ui/LazySlot";
import { Skeleton } from "../ui/Spinner";
import { toast } from "../ui/toast";
import { useColumns } from "../ui/virtual";
import { type AlbumOut, type AlbumPhotoItem, albumNode, type PhotoItem, periodOf, photosApi } from "./api";
import { downloadZip, SelectionBar, usePhotoBrowser, useStoreVersion } from "./browse";
import { GAP, type GridControl, MIN_CELL, PhotoGrid } from "./Grid";
import { reorders, useThumbPatches } from "./live";
import { SectionStore } from "./store";
import type { AlbumRuleProps } from "./people/AlbumRule";
import { buildLayout } from "./timeline";
import "./photos.css";
import "./people/people.css";

const AlbumRule = lazyWithAreas<AlbumRuleProps>(() => import("./people/AlbumRule"), ["photos_people"]);

export default function AlbumPage({ params }: RouteProps): JSX.Element {
  useScope("album");
  const id = params.id;
  const [album, setAlbum] = useState<AlbumOut | null>(null);
  const [gone, setGone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [count, setCount] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const control = useRef<GridControl | null>(null);
  const { columns, cellWidth } = useColumns(wrap, MIN_CELL, GAP);
  const rowHeight = Math.max(MIN_CELL / 2, Math.round(cellWidth)) + GAP;
  const owner = album?.permission === "owner";
  useNavSection(album && !owner ? "shared" : null);
  // Het merkje en "met een regel" komen uit het gebied photos_people: alleen voor de eigenaar laden
  const [peopleArea, setPeopleArea] = useState(() => areasLoaded(["photos_people"]));
  useEffect(() => {
    if (!owner || peopleArea) return;
    let live = true;
    loadAreas(["photos_people"]).then(
      () => live && setPeopleArea(true),
      () => {},
    );
    return () => {
      live = false;
    };
  }, [owner, peopleArea]);
  // De delingen toen het deeldialoog openging (voor de vraag van Keuze 42)
  const sharesBefore = useRef<Promise<Set<string> | null> | null>(null);

  const store = useMemo(() => new SectionStore((_s, after) => photosApi.albumItems(id, after)), [id]);
  const version = useStoreVersion(store);

  const load = useCallback(async (): Promise<AlbumOut | null> => {
    try {
      const a = await photosApi.album(id);
      setAlbum(a);
      setCount(a.count);
      store.setSections([a.count], undefined, () => 0);
      setError(null);
      setGone(null);
      return a;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setGone(t("photos.albumPage.gone"));
      else setError(errorText(e));
      return null;
    }
  }, [id, store]);

  useEffect(() => {
    store.onMismatch = (_s, got) => {
      // Er kwam iets bij of ging weg terwijl we laadden: het aantal van de items zelf geldt
      setCount(got);
      store.setSections([got], undefined, () => 0);
    };
    store.onError = (e) => setError(errorText(e));
    void load();
  }, [store, load]);

  useServerEvents((e) => {
    if (e.type === "connected" || e.type.startsWith("share.")) void load();
    else if ((e.type === "album.changed" || e.type === "album.deleted") && String(e.data.id) === id) void load();
    else if (reorders(e) || e.type.startsWith("node.")) {
      const ids = (e.data.node_ids ?? e.data.ids ?? [e.data.id]) as unknown[];
      if (ids.some((x) => store.indexOf(String(x)) >= 0)) void load();
    }
  });
  // Een thumbnail die klaar is: alleen die foto bijwerken (live.ts)
  useThumbPatches(store);

  const layout = useMemo(() => buildLayout([count], columns, rowHeight, 0, false), [count, columns, rowHeight]);
  const section = useMemo(() => (version >= 0 ? (s: number) => store.section(s) : () => undefined), [store, version]);
  const onVisible = useCallback((sections: number[]) => store.want(sections), [store]);
  const fetchIds = useCallback(async () => photosApi.albumIds(id), [id]);
  const b = usePhotoBrowser({ store, layout, fetchIds, control, scope: "album" });
  const selected = b.selection.ids;
  const ids = useMemo(() => [...selected], [selected]);
  const tooBig = (album?.count ?? 0) > MAX_BATCH_IDS;
  const notes = owner && peopleArea;
  const note = useCallback(
    (it: PhotoItem) =>
      // De store kent alleen PhotoItem; wat albumItems gaf, is een AlbumPhotoItem
      notes && (it as AlbumPhotoItem).album?.shared === false
        ? t("photos_people.onlyYou")
        : null,
    [notes],
  );

  function openSharing() {
    setSharing(true);
    const rule = album?.rule;
    // Alleen als nieuwe foto's van de regel nog niet gedeeld worden, is er iets te vragen
    sharesBefore.current =
      album && rule && !rule.share_new
        ? api.shares(album.id).then(
            (list) => new Set(list.map((s) => s.id)),
            () => null,
          )
        : null;
  }

  /** Na een wijziging in het deeldialoog: is er een deling of link bij gekomen op een album met
   * een regel waarvan nieuwe foto's nog niet gedeeld worden, dan de vraag (Keuze 42). */
  async function afterShareChange() {
    const a = await load();
    const before = await sharesBefore.current;
    if (!a?.rule || a.rule.share_new || !before) return;
    try {
      const now = await api.shares(a.id);
      sharesBefore.current = Promise.resolve(new Set(now.map((s) => s.id)));
      if (!now.some((s) => !before.has(s.id))) return;
      const { askShareNew } = await import("./people/shareQuestion");
      await askShareNew(a.id, a.rule);
      void load();
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function downloadAll() {
    if (!album) return;
    try {
      const r = await photosApi.albumIds(album.id);
      if (r.truncated) toast(t("photos.albumPage.tooBig", { max: MAX_BATCH_IDS }), { kind: "error" });
      else await downloadZip(r.ids, album.name);
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function rename() {
    if (!album) return;
    const name = await prompt({
      title: t("photos.albumPage.rename"),
      label: t("photos.albumPage.name"),
      initial: album.name,
      confirm: t("common.save"),
      validate: (v) => (v.trim() ? null : t("photos.add.nameRequired")),
    });
    if (name === null || name.trim() === album.name) return;
    await change({ name: name.trim() });
  }

  async function describe() {
    if (!album) return;
    const description = await prompt({
      title: t("photos.albumPage.describe"),
      label: t("photos.albumPage.description"),
      initial: album.description,
      confirm: t("common.save"),
    });
    if (description === null) return;
    await change({ description });
  }

  async function change(c: { name?: string; description?: string; cover_id?: string | null }) {
    if (!album) return;
    try {
      setAlbum(await photosApi.changeAlbum(album.id, c));
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function remove(list: string[]) {
    if (!album || list.length === 0) return;
    try {
      const r = await photosApi.removeFromAlbum(album.id, list);
      toast(t("photos.albumPage.removed", { n: r.removed }), { kind: "ok" });
      b.clear();
      void load();
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  async function deleteAlbum() {
    if (!album) return;
    const ok = await confirm({
      title: t("photos.albumPage.delete"),
      message: t("photos.albumPage.deleteText", { name: album.name }),
      confirm: t("photos.albumPage.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await photosApi.deleteAlbum(album.id);
      toast(t("photos.albumPage.deleted", { name: album.name }), { kind: "ok" });
      navigate("/albums");
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  function coverFrom(list: string[]) {
    const pick = list.length === 1 ? list[0] : store.item(b.focus)?.id;
    if (pick) void change({ cover_id: pick });
  }

  const some = () => selected.size > 0;
  const mine = () => owner;
  useShortcuts(
    [
      {
        combo: "d",
        label: t("photos.keys.download"),
        run: () => void (selected.size ? downloadZip(ids, album?.name ?? t("photos.album")) : downloadAll()),
      },
      { combo: "s", label: t("photos.keys.share"), run: openSharing, when: mine },
      { combo: "f2", label: t("photos.keys.rename"), run: () => void rename(), when: mine },
      { combo: "c", label: t("photos.keys.cover"), run: () => coverFrom(ids), when: mine },
      {
        combo: ["delete", "mod+backspace"],
        label: t("photos.keys.remove"),
        run: () => void remove(ids),
        when: () => owner && some(),
      },
      { combo: "shift+delete", label: t("photos.keys.deleteAlbum"), run: () => void deleteAlbum(), when: mine },
    ],
    { scope: "album" },
  );

  if (gone) {
    return (
      <main className="wide">
        <PageTitle>{t("photos.album")}</PageTitle>
        <EmptyState icon="album" title={gone}>
          <p>
            <Link to="/albums">{t("photos.albums")}</Link>
          </p>
        </EmptyState>
      </main>
    );
  }

  const period = album ? periodOf(album) : "";
  return (
    <main className="wide album-page">
      <PageTitle
        actions={
          album && (
            <>
              <Button icon="download" disabled={tooBig || count === 0} onClick={() => void downloadAll()}>
                {t("photos.download")}
              </Button>
              {owner && (
                <>
                  <Button icon="share" onClick={openSharing}>
                    {t("photos.albumPage.share")}
                  </Button>
                  <LazySlot>
                    <AlbumRule album={album} onChanged={() => void load()} />
                  </LazySlot>
                  <Button icon="rename" onClick={() => void rename()}>
                    {t("photos.albumPage.rename")}
                  </Button>
                  <Button icon="document" onClick={() => void describe()}>
                    {t("photos.albumPage.describe")}
                  </Button>
                  <Button icon="trash" variant="danger" onClick={() => void deleteAlbum()}>
                    {t("photos.albumPage.delete")}
                  </Button>
                </>
              )}
            </>
          )
        }
      >
        {album?.name ?? t("photos.album")}
      </PageTitle>
      {album && (
        <p className="muted album-meta">
          {t("photos.count", { n: count })}
          {period && ` · ${period}`}
          {!owner && ` · ${t("photos.albumPage.by", { name: album.owner.display_name })}`}
          {owner && album.shared && ` · ${t("photos.albumPage.isShared")}`}
          {notes && album.rule && ` · ${t("photos_people.rule.active")}`}
        </p>
      )}
      {album?.description && <p className="album-description">{album.description}</p>}
      {tooBig && <p className="muted">{t("photos.albumPage.tooBig", { max: MAX_BATCH_IDS })}</p>}
      {error && <p role="alert">{error}</p>}
      <div ref={wrap} className="photos-wrap">
        {!album && !error && <Skeleton />}
        {album && count === 0 && (
          <EmptyState icon="album" title={t("photos.albumPage.empty")}>
            {owner && (
              <p>
                <Link to="/fotos">{t("photos.albumPage.emptyHint")}</Link>
              </p>
            )}
          </EmptyState>
        )}
        {album && count > 0 && (
          <PhotoGrid
            layout={layout}
            rowHeight={rowHeight}
            headerHeight={0}
            section={section}
            selection={b.selection}
            focus={b.focus}
            setFocus={b.setFocus}
            label={t("photos.albumPage.grid", { name: album.name })}
            onVisible={onVisible}
            onCellClick={b.onCellClick}
            onShiftMove={b.onShiftMove}
            control={control}
            note={note}
          />
        )}
      </div>
      <SelectionBar count={selected.size} onClear={b.clear} onGone={b.focusGrid}>
        <Button icon="download" onClick={() => void downloadZip(ids, album?.name ?? t("photos.album"))}>
          {t("photos.download")}
        </Button>
        {owner && selected.size === 1 && (
          <Button icon="image" onClick={() => coverFrom(ids)}>
            {t("photos.albumPage.cover")}
          </Button>
        )}
        {owner && (
          <Button icon="close" variant="danger" onClick={() => void remove(ids)}>
            {t("photos.albumPage.remove")}
          </Button>
        )}
      </SelectionBar>
      {sharing && album && (
        <ShareDialog
          node={albumNode(album)}
          onClose={() => setSharing(false)}
          onChanged={() => void afterShareChange()}
        />
      )}
      {b.viewer}
    </main>
  );
}
