// De deeldialoog: delen met personen en groepen (met rechten en een vervaldatum) en publieke
// links. Kiezen in het zoekveld deelt nog niet: wie je kiest, komt op een lijst, en Delen deelt
// met iedereen op die lijst, met de rechten en de vervaldatum die ernaast staan. Met nodes (meer
// dan één) deelt hij een selectie in één keer met die personen en groepen: een lus over
// POST /files/{id}/shares met voortgang. Een album deel je alleen om te bekijken en zonder
// uploadlink. Alt+P: zoeken naar personen; Alt+L: een nieuwe link.

import { type FormEvent, type JSX, type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type FileNode, type Share } from "../api";
import { errorText, fmt, t } from "../i18n";
import {
  checkedExpiry,
  dateInputValue,
  type Excluded,
  excluding,
  expiryLabel,
  isExcluded,
  type LinkForm,
  linkChange,
  linkHref,
  linkKinds,
  linkSummary,
  MAX_LINK_PASSWORD,
  MAX_MULTI_SHARE,
  MIN_LINK_PASSWORD,
  memberCount,
  multiShare,
  newLink,
  PERMISSION_LABELS,
  type ShareResult,
  shareEach,
  sharePermissions,
  today,
  withPending,
} from "../sharing";
import type { ShareDialogProps } from "../ShareDialog";
import { Button, IconButton } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { confirm } from "../ui/dialogs";
import { Field } from "../ui/Field";
import { useScope, useShortcuts } from "../ui/keys";
import { toast } from "../ui/toast";
import { type Option, PersonPicker } from "./PersonPicker";
import "../share.css";
import "../files.css";

/** Delen met personen en groepen, en publieke links. Alleen voor de eigenaar. */
export default function ShareDialogPanel({ node, nodes, onClose, onChanged }: ShareDialogProps): JSX.Element {
  useScope("share");
  if (nodes && nodes.length > 1) return <MultiShareDialog nodes={nodes} onClose={onClose} onChanged={onChanged} />;
  return <SingleShareDialog node={node} onClose={onClose} onChanged={onChanged} />;
}

function SingleShareDialog({
  node,
  onClose,
  onChanged,
}: {
  node: FileNode;
  onClose(): void;
  onChanged(): void;
}): JSX.Element {
  const [shares, setShares] = useState<Share[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [linkOpen, setLinkOpen] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  /** Alt+P terwijl de delingen nog laden: het zoekveld is er dan nog niet. Hier staat waar de
   * focus toen stond; zodra het veld er is en de focus daar nog staat, gaat hij naar het veld. */
  const searchWanted = useRef<Element | null | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      setShares(await api.shares(node.id));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [node.id]);

  useEffect(() => {
    void load();
  }, [load]);

  useShortcuts(
    [
      {
        combo: "alt+p",
        label: t("sharing.keys.people"),
        run: () => {
          if (search.current) search.current.focus();
          else searchWanted.current = document.activeElement;
        },
        allowInInput: true,
      },
      { combo: "alt+l", label: t("sharing.keys.newLink"), run: () => setLinkOpen(true), allowInInput: true },
    ],
    { scope: "share" },
  );

  useEffect(() => {
    const was = searchWanted.current;
    if (shares === null || was === undefined) return;
    searchWanted.current = undefined;
    if (document.activeElement === was) search.current?.focus();
  }, [shares]);

  /** Na een wijziging: de lijst hier en het "gedeeld"-merkje in de lijst met bestanden. */
  const changed = useCallback(async () => {
    await load();
    onChanged();
  }, [load, onChanged]);

  const people = shares?.filter((s) => s.type !== "link") ?? [];
  const links = shares?.filter((s) => s.type === "link") ?? [];

  return (
    <Dialog
      title={t("sharing.dialog.title", { name: node.name })}
      onClose={onClose}
      actions={<Button onClick={onClose}>{t("common.close")}</Button>}
    >
      {error && <p role="alert">{error}</p>}
      {shares === null && !error && <p className="muted">{t("common.loading")}</p>}
      {shares !== null && (
        <>
          <People node={node} shares={people} onChanged={changed} search={search} />
          <Links node={node} shares={links} onChanged={changed} open={linkOpen} setOpen={setLinkOpen} />
        </>
      )}
    </Dialog>
  );
}

/** Kwam de knop `to` net terug (shown) in de plaats van iets met de focus (een formulier dat
 * dichtging), dan de focus erop; anders zou hij op <body> staan, midden in de dialoog. */
function useFocusOnReturn(shown: boolean, to: { current: HTMLElement | null }): void {
  const before = useRef(shown);
  useEffect(() => {
    const back = shown && !before.current;
    before.current = shown;
    const now = document.activeElement;
    if (back && (now === null || now === document.body)) to.current?.focus();
  }, [shown, to]);
}

/** Een actie met een eigen foutmelding en bezig-stand. */
function useAction(onDone: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (fn: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      try {
        await fn();
        setError(null);
        await onDone();
        return true;
      } catch (e) {
        setError(errorText(e));
        // Een fout van de server (al gedeeld, intussen weg): dan klopt de lijst hier misschien
        // niet meer, bijv. omdat het net vanaf een ander apparaat gebeurde
        if (e instanceof ApiError) await onDone();
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onDone],
  );
  return { busy, error, setError, run };
}

function PermissionSelect({
  kind,
  value,
  label,
  disabled,
  onChange,
}: {
  kind: FileNode["kind"];
  value: "read" | "write";
  label: string;
  disabled?: boolean;
  onChange(v: "read" | "write"): void;
}): JSX.Element {
  const options = sharePermissions(kind);
  return (
    <select aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as "read" | "write")}>
      {options.map((p) => (
        <option key={p} value={p}>
          {PERMISSION_LABELS[p]}
        </option>
      ))}
    </select>
  );
}

/** Delen met wie op de lijst staat; geeft de keys van wie het helemaal lukte (die gaan van de
 * lijst af). permission is bij een album altijd "read". */
type ShareRun = (people: Option[], permission: "read" | "write", expires: string | null) => Promise<string[]>;

/**
 * Personen en groepen toevoegen. Kiezen in het zoekveld deelt nog niet: wie je kiest, komt op de
 * lijst "Klaar om te delen", met de rechten en de vervaldatum ernaast. Pas Delen (of Enter in het
 * lege zoekveld) deelt met iedereen op de lijst. Zo zie en kies je de rechten vóór er iets gedeeld
 * is, in welke volgorde je het ook doet; eerder deelde kiezen meteen, met de rechten die er op dat
 * moment stonden ("Kan bekijken"), en een latere keuze deed niets meer voor wie al gekozen was.
 */
function AddPeople({
  kind,
  exclude,
  busy,
  search,
  onShare,
  onError,
}: {
  /** Een album deel je alleen om te bekijken: dan geen keuze voor de rechten. */
  kind: FileNode["kind"];
  /** Wie al een deling heeft */
  exclude: Excluded;
  /** Er loopt al iets anders (wijzigen of stoppen met delen) */
  busy: boolean;
  search: RefObject<HTMLInputElement | null>;
  onShare: ShareRun;
  onError(message: string | null): void;
}): JSX.Element {
  const album = kind === "album";
  const [pending, setPending] = useState<readonly Option[]>([]);
  const [permission, setPermission] = useState<"read" | "write">("read");
  const [date, setDate] = useState("");
  const [running, setRunning] = useState(false);
  /** Voor schermlezers: wie er net op de lijst kwam of eraf ging. */
  const [said, setSaid] = useState("");
  /** Tegen dubbel delen (dubbelklik, of Enter en een klik): de state is pas na de render bij. */
  const started = useRef(false);
  const removeButtons = useRef(new Map<string, HTMLButtonElement>());
  /** Na weghalen: de focus naar de knop van de volgende (key), of null: het zoekveld. */
  const focusAfter = useRef<string | null | undefined>(undefined);

  // Wie intussen een deling heeft (een fout "al gedeeld", of gedeeld vanaf een ander apparaat),
  // staat niet meer klaar
  const list = pending.filter((p) => !isExcluded(p.target, exclude));
  const blocked = busy || running;

  // En daarna ook echt van de lijst af: anders kwam hij terug als je later stopt met delen
  useEffect(() => {
    setPending((all) => (all.some((p) => isExcluded(p.target, exclude)) ? all.filter((p) => !isExcluded(p.target, exclude)) : all));
  }, [exclude]);

  useEffect(() => {
    const key = focusAfter.current;
    if (key === undefined) return;
    focusAfter.current = undefined;
    const button = key === null ? undefined : removeButtons.current.get(key);
    if (button) button.focus();
    else search.current?.focus();
  }, [pending, search]);

  function add(o: Option): boolean {
    setPending((all) => withPending(all, o));
    setSaid(t("sharing.people.pendingAdded", { name: o.name }));
    return true;
  }

  function drop(o: Option) {
    const i = list.findIndex((p) => p.key === o.key);
    focusAfter.current = (list[i + 1] ?? list[i - 1])?.key ?? null;
    setPending((all) => all.filter((p) => p.key !== o.key));
    setSaid(t("sharing.people.pendingRemoved", { name: o.name }));
  }

  async function share() {
    if (started.current || blocked || list.length === 0) return;
    let expires: string | null = null;
    try {
      expires = checkedExpiry(date);
    } catch (e) {
      onError(errorText(e));
      return;
    }
    started.current = true;
    setRunning(true);
    setSaid("");
    try {
      const done = new Set(await onShare([...list], album ? "read" : permission, expires));
      setPending((all) => all.filter((p) => !done.has(p.key)));
      if (list.every((p) => done.has(p.key))) setDate("");
    } finally {
      started.current = false;
      setRunning(false);
      // Terug in het zoekveld, voor de volgende (de knop Delen is nu uitgeschakeld)
      search.current?.focus();
    }
  }

  return (
    <>
      <div className="share-add">
        <PersonPicker
          exclude={excluding(exclude, list)}
          busy={blocked}
          onPick={add}
          onSubmit={() => void share()}
          inputRef={search}
          onError={onError}
        />
      </div>
      {list.length > 0 && (
        <ul className="share-pending" aria-label={t("sharing.people.pending")}>
          {list.map((p) => (
            <li key={p.key}>
              <span>
                {p.name} <span className="muted">{p.detail}</span>
              </span>
              <IconButton
                ref={(b) => {
                  if (b) removeButtons.current.set(p.key, b);
                  else removeButtons.current.delete(p.key);
                }}
                icon="close"
                label={t("sharing.people.pendingRemove", { name: p.name })}
                disabled={blocked}
                onClick={() => drop(p)}
              />
            </li>
          ))}
        </ul>
      )}
      <p className="sr-only" role="status">
        {said}
      </p>
      <div className="share-add share-go">
        {!album && (
          <label className="share-date">
            <span>{t("sharing.people.permFor")}</span>
            <PermissionSelect
              kind={kind}
              value={permission}
              label={t("sharing.people.permFor")}
              disabled={blocked}
              onChange={setPermission}
            />
          </label>
        )}
        <label className="share-date">
          <span>{t("sharing.people.expiry")}</span>
          <input
            type="date"
            min={today()}
            value={date}
            disabled={blocked}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
        <Button
          variant="primary"
          icon="share"
          disabled={list.length === 0 || blocked}
          busy={running}
          onClick={() => void share()}
        >
          {t("sharing.people.share")}
        </Button>
      </div>
    </>
  );
}

function People({
  node,
  shares,
  onChanged,
  search,
}: {
  node: FileNode;
  shares: Share[];
  onChanged: () => Promise<void>;
  search: RefObject<HTMLInputElement | null>;
}): JSX.Element {
  const action = useAction(onChanged);
  const album = node.kind === "album";

  const users = new Set(shares.map((s) => s.user?.id).filter((x): x is string => Boolean(x)));
  const groups = new Set(shares.map((s) => s.group?.id).filter((x): x is string => Boolean(x)));

  /** Eén POST per persoon of groep, daarna één keer de lijst verversen en één melding. */
  async function share(people: Option[], permission: "read" | "write", expires: string | null): Promise<string[]> {
    let results: ShareResult<Option, string>[] = [];
    await action.run(async () => {
      results = await shareEach(people, [node.id], (who, id) =>
        api.share(id, who.target, permission, expires ?? undefined),
      );
    });
    const done = results.filter((r) => r.errors.length === 0).map((r) => r.who);
    if (done.length > 0) toast(t("sharing.people.added", { name: fmt.list(done.map((p) => p.name)) }), { kind: "ok" });
    const failed = results.filter((r) => r.errors.length > 0);
    if (failed.length > 0)
      action.setError(
        failed.map((r) => t("sharing.people.failed", { name: r.who.name, reason: errorText(r.errors[0]) })).join("\n"),
      );
    return done.map((p) => p.key);
  }

  async function remove(s: Share, name: string) {
    const yes = await confirm({
      title: t("sharing.people.removeTitle", { name }),
      message: t("sharing.people.removeText", { name }),
      confirm: t("sharing.people.removeConfirm"),
      danger: true,
    });
    if (yes) await action.run(() => api.deleteShare(s.id));
  }

  async function changeExpiry(s: Share, value: string) {
    try {
      const expires_at = value ? checkedExpiry(value) : null;
      await action.run(() => api.changeShare(s.id, { expires_at }));
    } catch (e) {
      action.setError(errorText(e));
    }
  }

  return (
    <section className="share-section" aria-labelledby="share-people">
      <h3 id="share-people">{t("sharing.people.title")}</h3>
      <AddPeople
        kind={node.kind}
        exclude={{ users, groups }}
        busy={action.busy}
        search={search}
        onShare={share}
        onError={action.setError}
      />
      {album && <p className="muted">{t("sharing.people.albumNote")}</p>}
      {action.error && (
        <p role="alert" className="share-errors">
          {action.error}
        </p>
      )}
      {shares.length === 0 ? (
        <p className="muted">{t("sharing.people.nobody")}</p>
      ) : (
        <ul className="share-list" aria-label={t("sharing.people.sharedWith")}>
          {shares.map((s) => {
            const name = s.user?.display_name ?? s.group?.name ?? t("sharing.people.unknown");
            const expiry = expiryLabel(s.expires_at);
            const detail = s.user
              ? s.user.email
              : t("sharing.people.group", { members: memberCount(s.group?.member_count ?? 0) });
            return (
              <li key={s.id}>
                <div className="share-who">
                  <strong>{name}</strong>{" "}
                  <span className="muted">
                    {detail}
                    {expiry && ` · ${expiry}`}
                  </span>
                </div>
                <div className="share-actions">
                  <PermissionSelect
                    kind={node.kind}
                    value={s.permission === "write" ? "write" : "read"}
                    label={t("sharing.people.permOf", { name })}
                    disabled={action.busy || album}
                    onChange={(p) => void action.run(() => api.changeShare(s.id, { permission: p }))}
                  />
                  <input
                    type="date"
                    aria-label={t("sharing.people.expiryOf", { name })}
                    min={today()}
                    value={dateInputValue(s.expires_at)}
                    disabled={action.busy}
                    onChange={(e) => void changeExpiry(s, e.target.value)}
                  />
                  <Button
                    disabled={action.busy}
                    aria-label={t("sharing.people.removeLabel", { name })}
                    onClick={() => void remove(s, name)}
                  >
                    {t("sharing.people.remove")}
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Links({
  node,
  shares,
  onChanged,
  open,
  setOpen,
}: {
  node: FileNode;
  shares: Share[];
  onChanged: () => Promise<void>;
  open: boolean;
  setOpen(v: boolean): void;
}): JSX.Element {
  return (
    <section className="share-section" aria-labelledby="share-links">
      <h3 id="share-links">{t("sharing.links.title")}</h3>
      <p className="muted">{t("sharing.links.help")}</p>
      {shares.length > 0 && (
        <ul className="share-list" aria-label={t("sharing.links.list")}>
          {shares.map((s) => (
            <LinkRow key={s.id} share={s} onChanged={onChanged} />
          ))}
        </ul>
      )}
      <NewLinkForm node={node} onChanged={onChanged} open={open} setOpen={setOpen} />
    </section>
  );
}

function LinkRow({ share, onChanged }: { share: Share; onChanged: () => Promise<void> }): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  useFocusOnReturn(!editing, editButton);
  const action = useAction(onChanged);
  const href = linkHref(share, window.location.origin);
  const title = share.link?.label || t("sharing.links.link");

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  async function copy() {
    setHint(null);
    try {
      // Het klembord werkt alleen via https of localhost, niet via http op het LAN-adres
      if (!navigator.clipboard) throw new Error("clipboard");
      await navigator.clipboard.writeText(href);
      setCopied(true);
    } catch {
      input.current?.focus();
      input.current?.select();
      let done = false;
      try {
        done = document.execCommand("copy");
      } catch {
        done = false;
      }
      if (done) setCopied(true);
      else setHint(t("sharing.links.copyHint"));
    }
  }

  async function remove() {
    const yes = await confirm({
      title: t("sharing.links.removeTitle", { title }),
      message: t("sharing.links.removeText"),
      confirm: t("sharing.links.remove"),
      danger: true,
    });
    if (yes) await action.run(() => api.deleteShare(share.id));
  }

  return (
    <li className="share-link">
      <div className="share-who">
        <strong>{title}</strong> <span className="muted">{linkSummary(share)}</span>
      </div>
      <input
        ref={input}
        className="share-url"
        readOnly
        value={href}
        aria-label={t("sharing.links.address", { title })}
        onFocus={(e) => e.target.select()}
      />
      <div className="share-actions">
        <Button onClick={() => void copy()}>{copied ? t("sharing.links.copied") : t("sharing.links.copy")}</Button>
        <Button ref={editButton} aria-expanded={editing} onClick={() => setEditing((v) => !v)}>
          {t("sharing.links.edit")}
        </Button>
        <Button disabled={action.busy} onClick={() => void remove()}>
          {t("sharing.links.remove")}
        </Button>
      </div>
      {hint && (
        <p className="muted" role="status">
          {hint}
        </p>
      )}
      {action.error && <p role="alert">{action.error}</p>}
      {editing && (
        <LinkFields
          kinds={null}
          initial={{
            permission: share.permission === "upload" ? "upload" : "read",
            date: dateInputValue(share.expires_at),
            password: "",
            removePassword: false,
            label: share.link?.label ?? "",
          }}
          hasPassword={share.link?.has_password ?? false}
          submit={t("common.save")}
          busy={action.busy}
          onSubmit={async (form) => {
            if (
              await action.run(async () => {
                const change = linkChange(share, form);
                if (Object.keys(change).length) await api.changeShare(share.id, change);
              })
            )
              setEditing(false);
          }}
          onCancel={() => setEditing(false)}
        />
      )}
    </li>
  );
}

function NewLinkForm({
  node,
  onChanged,
  open,
  setOpen,
}: {
  node: FileNode;
  onChanged: () => Promise<void>;
  open: boolean;
  setOpen(v: boolean): void;
}): JSX.Element {
  const action = useAction(onChanged);
  // Na Link maken of Annuleren verdwijnt het formulier met de focus: dan op "Nieuwe link"
  const newButton = useRef<HTMLButtonElement>(null);
  useFocusOnReturn(!open, newButton);
  if (!open)
    return (
      <div className="toolbar">
        <Button ref={newButton} aria-keyshortcuts="Alt+L" onClick={() => setOpen(true)}>
          {t("sharing.links.new")}
        </Button>
      </div>
    );
  return (
    <div className="share-new">
      <h4>{t("sharing.links.new")}</h4>
      <LinkFields
        kinds={linkKinds(node.kind)}
        autoFocus
        initial={{ permission: "read", date: "", password: "", removePassword: false, label: "" }}
        hasPassword={false}
        submit={t("sharing.links.create")}
        busy={action.busy}
        onSubmit={async (form) => {
          if (await action.run(() => api.createLink(node.id, newLink(form)))) setOpen(false);
        }}
        onCancel={() => setOpen(false)}
      />
      {action.error && <p role="alert">{action.error}</p>}
    </div>
  );
}

/** De velden van een link. kinds: bij een nieuwe link de soorten die er zijn (uploaden alleen
 * bij een map); null bij wijzigen. */
function LinkFields({
  kinds,
  initial,
  hasPassword,
  submit,
  busy,
  autoFocus,
  onSubmit,
  onCancel,
}: {
  kinds: ("read" | "upload")[] | null;
  initial: LinkForm;
  hasPassword: boolean;
  submit: string;
  busy: boolean;
  autoFocus?: boolean;
  onSubmit: (form: LinkForm) => void;
  onCancel: () => void;
}): JSX.Element {
  const [form, setForm] = useState(initial);
  const set = (change: Partial<LinkForm>) => setForm((f) => ({ ...f, ...change }));
  const first = useRef<HTMLSelectElement & HTMLInputElement>(null);
  useEffect(() => {
    if (autoFocus) first.current?.focus();
  }, [autoFocus]);

  function send(e: FormEvent) {
    e.preventDefault();
    onSubmit(form);
  }

  return (
    <form className="share-form" onSubmit={send}>
      {kinds !== null && (
        <Field
          label={t("sharing.links.kind")}
          hint={form.permission === "upload" ? t("sharing.links.helpUpload") : t("sharing.links.helpRead")}
        >
          {(c) => (
            <select
              {...c}
              ref={first}
              value={form.permission}
              onChange={(e) => set({ permission: e.target.value as "read" | "upload" })}
            >
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {k === "upload" ? t("sharing.links.kindUpload") : t("sharing.links.kindRead")}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      {hasPassword && (
        <label className="check">
          <input
            type="checkbox"
            checked={form.removePassword}
            onChange={(e) => set({ removePassword: e.target.checked, password: "" })}
          />
          {t("sharing.links.removePassword")}
        </label>
      )}
      {!form.removePassword && (
        <Field
          label={hasPassword ? t("sharing.links.newPassword") : t("sharing.links.password")}
          hint={t("sharing.links.passwordHint", { min: MIN_LINK_PASSWORD })}
        >
          {(c) => (
            <input
              {...c}
              type="password"
              autoComplete="new-password"
              minLength={MIN_LINK_PASSWORD}
              maxLength={MAX_LINK_PASSWORD}
              value={form.password}
              onChange={(e) => set({ password: e.target.value })}
            />
          )}
        </Field>
      )}
      <Field label={t("sharing.links.validUntil")}>
        {(c) => <input {...c} type="date" min={today()} value={form.date} onChange={(e) => set({ date: e.target.value })} />}
      </Field>
      <Field label={t("sharing.links.label")}>
        {(c) => (
          <input
            {...c}
            maxLength={100}
            placeholder={t("sharing.links.labelPlaceholder")}
            value={form.label}
            onChange={(e) => set({ label: e.target.value })}
          />
        )}
      </Field>
      <div className="share-actions">
        <Button onClick={onCancel}>{t("common.cancel")}</Button>
        <Button type="submit" variant="primary" busy={busy}>
          {submit}
        </Button>
      </div>
    </form>
  );
}

/** Een selectie (≤ 100) in één keer delen met de personen en groepen op de lijst. */
function MultiShareDialog({
  nodes,
  onClose,
  onChanged,
}: {
  nodes: FileNode[];
  onClose(): void;
  onChanged(): void;
}): JSX.Element {
  const plan = multiShare(nodes, MAX_MULTI_SHARE);
  const album = plan.ok.some((n) => n.kind === "album");
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const empty = useRef<Excluded>({ users: new Set(), groups: new Set() }).current;

  useShortcuts(
    [{ combo: "alt+p", label: t("sharing.keys.people"), run: () => search.current?.focus(), allowInInput: true }],
    { scope: "share" },
  );

  /** Elk item met elke persoon of groep: één POST per keer, met voortgang. */
  async function share(people: Option[], permission: "read" | "write", expires: string | null): Promise<string[]> {
    setError(null);
    setProgress({ done: 0, total: plan.ok.length * people.length });
    const results = await shareEach(
      people,
      plan.ok,
      (who, n) => api.share(n.id, who.target, permission, expires ?? undefined),
      (done, total) => setProgress({ done, total }),
    );
    setProgress(null);
    const shared = results.filter((r) => r.ok.length > 0);
    if (shared.length > 0) {
      const n = new Set(shared.flatMap((r) => r.ok.map((x) => x.id))).size;
      toast(t("sharing.many.done", { n, names: fmt.list(shared.map((r) => r.who.name)) }), { kind: "ok" });
    }
    const failed = results.filter((r) => r.errors.length > 0);
    if (failed.length > 0)
      setError(
        failed
          .map((r) => t("sharing.many.failed", { n: r.errors.length, name: r.who.name, reason: errorText(r.errors[0]) }))
          .join("\n"),
      );
    onChanged();
    return results.filter((r) => r.errors.length === 0).map((r) => r.who.key);
  }

  return (
    <Dialog
      title={t("sharing.dialog.titleMany", { n: nodes.length })}
      onClose={onClose}
      actions={<Button onClick={onClose}>{t("common.close")}</Button>}
    >
      {plan.tooMany ? (
        <p role="alert">{t("sharing.many.tooMany", { max: MAX_MULTI_SHARE, n: nodes.length })}</p>
      ) : (
        <>
          <p>{t("sharing.many.intro", { n: plan.ok.length })}</p>
          <section className="share-section" aria-labelledby="share-many-people">
            <h3 id="share-many-people">{t("sharing.people.title")}</h3>
            {plan.ok.length === 0 ? (
              <p className="muted">{t("sharing.many.nothing")}</p>
            ) : (
              <AddPeople
                kind={album ? "album" : "dir"}
                exclude={empty}
                busy={progress !== null}
                search={search}
                onShare={share}
                onError={setError}
              />
            )}
            {album && <p className="muted">{t("sharing.people.albumNote")}</p>}
            {progress && (
              <div className="share-progress">
                <progress max={progress.total} value={progress.done} aria-label={t("sharing.many.items")} />
                <span aria-live="polite">{t("sharing.many.progress", progress)}</span>
              </div>
            )}
            {error && (
              <p role="alert" className="share-errors">
                {error}
              </p>
            )}
          </section>
          <section className="share-section" aria-labelledby="share-many-items">
            <h3 id="share-many-items">{t("sharing.many.items")}</h3>
            <ul className="share-list share-items">
              {plan.ok.map((n) => (
                <li key={n.id}>{n.name}</li>
              ))}
              {plan.skipped.map(({ node, reason }) => (
                <li key={node.id} className="share-skipped" aria-disabled="true">
                  <span>{node.name}</span> <span className="muted">{reason}</span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </Dialog>
  );
}
