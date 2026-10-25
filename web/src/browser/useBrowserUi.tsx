// Wat de acties uit het register (actions.ts) echt doen: dialogen openen (verplaatsen,
// kopiëren, tags, delen, versies, album), hernoemen, downloaden (een bestand, of een zip) en de
// bulkacties van de API. Bestanden (Files.tsx), NodeActions (⋯ bij één node, ook in Gedeeld met
// mij) en Zoeken gebruiken dit allemaal; de pagina krijgt de uitkomst terug (upsert/remove) en
// werkt zijn lijst bij zonder alles opnieuw te laden.

import { type ReactNode, Suspense, useMemo, useRef, useState } from "react";
import { api, type FileNode } from "../api";
import { crossApi, type ItemResult } from "../crossApi";
import { FolderPicker } from "../FolderPicker";
import { errorText, t } from "../i18n";
import { nodeHref } from "../links";
import { AddToAlbumDialog } from "../photos/index";
import { navigate } from "../router";
import { ShareDialog } from "../ShareDialog";
import { canWrite, permissionOf } from "../sharing";
import { TagEditor } from "../Tags";
import { prompt } from "../ui/dialogs";
import { announce, toast } from "../ui/toast";
import { Versions } from "../Versions";
import type { BrowserUi } from "./actions";
import { clipboard, ownerOf, planTransfer } from "./clipboard";
import { offerUndo } from "./undo";

export interface UiHooks {
  /** De map die open staat (begin van de kiezer, doel van plakken); null: geen (Zoeken) */
  folder: FileNode | null;
  me: string | null;
  /** Waar de kiezer begint (standaard: folder) */
  startId?: string | null;
  /** Een fout tonen; zonder: als melding (toast) */
  onError?(message: string): void;
  /** Een bestand openen; zonder: naar de map met ?bestand= */
  openFile?(node: FileNode): void;
  /** Deze nodes zijn nieuw of veranderd (zoals de API ze teruggaf) */
  onUpsert?(nodes: FileNode[]): void;
  /** Deze ids staan niet meer in de map die open staat */
  onRemove?(ids: string[]): void;
  /** Deze nodes zijn veranderd op een manier die de API niet teruggaf (tags, delingen, versies):
   * opnieuw opvragen */
  onRefresh?(ids: string[]): void;
}

type Open =
  | { kind: "move" | "copy"; nodes: readonly FileNode[] }
  | { kind: "tags"; node: FileNode }
  | { kind: "share"; nodes: readonly FileNode[] }
  | { kind: "versions"; node: FileNode }
  | { kind: "album"; nodes: readonly FileNode[] };

/** Klikt een downloadlink aan (een bestand of een zip): de browser downloadt hem. */
function follow(url: string, name: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}


/** Meldt wat er misging bij een bulkactie (de eerste fout, vertaald op code). */
export function reportFailures(results: readonly ItemResult[]): void {
  const failed = results.filter((r) => !r.ok);
  if (!failed.length) return;
  toast(t("browser.failed", { n: failed.length, error: errorText(failed[0]) }), { kind: "error" });
}

function ok(results: readonly ItemResult[]): ItemResult[] {
  return results.filter((r) => r.ok);
}

export interface BrowserUiResult {
  ui: BrowserUi;
  /** Dialogen die open staan: zet ze ergens in de pagina */
  dialogs: ReactNode;
  /** Verplaatsen of kopiëren naar map target (slepen, plakken); true als alles lukte */
  transfer(nodes: readonly FileNode[], target: FileNode, mode: "cut" | "copy"): Promise<boolean>;
}

export function useBrowserUi(h: UiHooks): BrowserUiResult {
  const [open, setOpen] = useState<Open | null>(null);
  const latest = useRef(h);
  latest.current = h;
  const close = () => setOpen(null);

  const { ui, transfer } = useMemo(() => {
    const hooks = () => latest.current;
    const fail = (e: unknown) => {
      const onError = hooks().onError;
      if (onError) onError(errorText(e));
      else toast(errorText(e), { kind: "error" });
    };

    async function transfer(nodes: readonly FileNode[], target: FileNode, mode: "cut" | "copy"): Promise<boolean> {
      const { folder, me, onUpsert, onRemove } = hooks();
      const plan = planTransfer(nodes, target, mode, me);
      const here = folder?.id === target.id;
      let all = true;
      try {
        if (plan.move.length) {
          const { results } = await crossApi.move(plan.move, target.id, "rename");
          const done = ok(results);
          if (here) onUpsert?.(done.map((r) => r.node).filter((n): n is FileNode => !!n));
          else onRemove?.(done.map((r) => r.id));
          reportFailures(results);
          all &&= done.length === results.length;
          if (done.length) announce(t("browser.moved", { n: done.length }));
        }
        if (plan.copy.length) {
          const { results } = await crossApi.copy(plan.copy, target.id);
          const done = ok(results);
          if (here) onUpsert?.(done.map((r) => r.node).filter((n): n is FileNode => !!n));
          reportFailures(results);
          all &&= done.length === results.length;
          if (done.length) announce(t("browser.copiedTo", { n: done.length }));
        }
      } catch (e) {
        fail(e);
        return false;
      }
      return all;
    }

    const ui: BrowserUi = {
      open(node) {
        if (node.kind === "file") {
          const openFile = hooks().openFile;
          if (openFile) openFile(node);
          else navigate(nodeHref(node));
        } else navigate(nodeHref(node));
      },
      download(nodes) {
        if (nodes.length === 1 && nodes[0].kind === "file") {
          api.downloadUrl(nodes[0].id).then((url) => follow(url, nodes[0].name), fail);
          return;
        }
        const name = nodes.length === 1 ? nodes[0].name : (hooks().folder?.name || t("browser.title"));
        crossApi.archiveLink(
          nodes.map((n) => n.id),
          name,
        ).then((link) => {
          announce(t("browser.zipStarted"));
          follow(link.url, `${name}.zip`);
        }, fail);
      },
      async rename(node) {
        const name = (
          await prompt({
            title: t("browser.rename.title"),
            label: t("browser.rename.label"),
            initial: node.name,
            confirm: t("browser.rename.confirm"),
            selectBaseName: node.kind === "file",
            validate: (v) => (v.trim() ? null : t("common.dialog.required")),
          })
        )?.trim();
        if (!name || name === node.name) return;
        try {
          hooks().onUpsert?.([await api.change(node.id, { name })]);
        } catch (e) {
          fail(e);
        }
      },
      cut(nodes) {
        clipboard.set("cut", nodes, hooks().folder?.id ?? null);
        announce(t("browser.cutDone", { n: nodes.length }));
      },
      copy(nodes) {
        clipboard.set("copy", nodes, hooks().folder?.id ?? null);
        announce(t("browser.copied", { n: nodes.length }));
      },
      async paste() {
        const clip = clipboard.getSnapshot();
        const target = hooks().folder;
        if (!clip || !target) return;
        const done = await transfer(clip.nodes, target, clip.mode);
        if (clip.mode === "cut" && done) clipboard.clear();
      },
      move(nodes) {
        setOpen({ kind: "move", nodes });
      },
      copyTo(nodes) {
        setOpen({ kind: "copy", nodes });
      },
      tags(node) {
        setOpen({ kind: "tags", node });
      },
      share(nodes) {
        setOpen({ kind: "share", nodes });
      },
      versions(node) {
        setOpen({ kind: "versions", node });
      },
      addToAlbum(nodes) {
        setOpen({ kind: "album", nodes });
      },
      async trash(nodes) {
        const ids = nodes.map((n) => n.id);
        try {
          const { results } = await crossApi.trash(ids);
          const gone = ok(results).map((r) => r.id);
          hooks().onRemove?.(gone);
          clipboard.drop(new Set(gone));
          reportFailures(results);
          if (!gone.length) return;
          const first = nodes.find((n) => n.id === gone[0]);
          const restore = () => {
            crossApi.restore(gone).then(({ results: back }) => {
              const restored = ok(back).map((r) => r.id);
              if (restored.length) {
                hooks().onRefresh?.(restored);
                announce(t("browser.restored", { n: restored.length }));
              }
              reportFailures(back);
            }, fail);
          };
          offerUndo(t("browser.trashed", { n: gone.length, name: first?.name ?? "" }), t("browser.undo"), restore);
        } catch (e) {
          fail(e);
        }
      },
    };
    return { ui, transfer };
  }, []);

  let dialogs: ReactNode = null;
  if (open?.kind === "move" || open?.kind === "copy") {
    const nodes = open.nodes;
    const move = open.kind === "move";
    const vars = { n: nodes.length, name: nodes[0]?.name ?? "" };
    const me = h.me;
    dialogs = (
      <FolderPicker
        title={t(move ? "browser.picker.moveTitle" : "browser.picker.copyTitle", vars)}
        action={t(move ? "browser.picker.moveHere" : "browser.picker.copyHere")}
        startId={h.startId !== undefined ? h.startId : (h.folder?.id ?? null)}
        exclude={new Set(nodes.filter((n) => n.kind === "dir").map((n) => n.id))}
        check={(folder) => {
          if (!canWrite(permissionOf(folder))) return t("browser.reasons.readOnly");
          if (move && nodes.some((n) => ownerOf(n, me) !== ownerOf(folder, me))) return t("browser.reasons.crossOwner");
          return null;
        }}
        onPick={(_id, folder) => {
          close();
          void (async () => {
            const { folder: here, onUpsert, onRemove } = latest.current;
            try {
              const ids = nodes.map((n) => n.id);
              const { results } = move ? await crossApi.move(ids, folder.id, "rename") : await crossApi.copy(ids, folder.id);
              const done = ok(results);
              if (here?.id === folder.id) onUpsert?.(done.map((r) => r.node).filter((n): n is FileNode => !!n));
              else if (move) onRemove?.(done.map((r) => r.id));
              reportFailures(results);
              if (done.length) announce(t(move ? "browser.moved" : "browser.copiedTo", { n: done.length }));
            } catch (e) {
              const onError = latest.current.onError;
              if (onError) onError(errorText(e));
              else toast(errorText(e), { kind: "error" });
            }
          })();
        }}
        onClose={close}
      />
    );
  } else if (open?.kind === "tags") {
    const id = open.node.id;
    dialogs = <TagEditor node={open.node} onClose={close} onSaved={() => latest.current.onRefresh?.([id])} />;
  } else if (open?.kind === "share") {
    const ids = open.nodes.map((n) => n.id);
    dialogs = (
      <ShareDialog
        node={open.nodes[0]}
        nodes={open.nodes.length > 1 ? [...open.nodes] : undefined}
        onClose={close}
        onChanged={() => latest.current.onRefresh?.(ids)}
      />
    );
  } else if (open?.kind === "versions") {
    const id = open.node.id;
    dialogs = <Versions node={open.node} onClose={close} onChanged={() => latest.current.onRefresh?.([id])} />;
  } else if (open?.kind === "album") {
    dialogs = (
      <Suspense fallback={null}>
        <AddToAlbumDialog nodeIds={open.nodes.map((n) => n.id)} onClose={close} />
      </Suspense>
    );
  }
  return { ui, dialogs, transfer };
}
