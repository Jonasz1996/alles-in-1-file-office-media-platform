import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type Directory, type FileNode, type Share } from "../api";
import { Dialog } from "../Dialog";
import {
  dateInputValue,
  expiryLabel,
  type LinkForm,
  linkChange,
  linkHref,
  linkSummary,
  MAX_LINK_PASSWORD,
  MIN_LINK_PASSWORD,
  memberCount,
  newLink,
  PERMISSION_LABELS,
  today,
} from "../sharing";
import type { ShareDialogProps } from "../ShareDialog";
import "../share.css";
import "../files.css";

/** Delen met personen en groepen, en publieke links. Alleen voor de eigenaar. */
export default function ShareDialogPanel({ node, onClose, onChanged }: ShareDialogProps) {
  const [shares, setShares] = useState<Share[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setShares(await api.shares(node.id));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [node.id]);

  useEffect(() => {
    load();
  }, [load]);

  /** Na een wijziging: de lijst hier en het "gedeeld"-merkje in de lijst met bestanden. */
  const changed = useCallback(async () => {
    await load();
    onChanged();
  }, [load, onChanged]);

  const people = shares?.filter((s) => s.type !== "link") ?? [];
  const links = shares?.filter((s) => s.type === "link") ?? [];

  return (
    <Dialog title={`"${node.name}" delen`} onClose={onClose}>
      {error && <p role="alert">{error}</p>}
      {shares === null && !error && <p className="muted">Laden…</p>}
      {shares !== null && (
        <>
          <People node={node} shares={people} onChanged={changed} />
          <Links node={node} shares={links} onChanged={changed} />
        </>
      )}
      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Sluiten
        </button>
      </div>
    </Dialog>
  );
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
        setError((e as Error).message);
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

function People({ node, shares, onChanged }: { node: FileNode; shares: Share[]; onChanged: () => Promise<void> }) {
  const [q, setQ] = useState("");
  const [permission, setPermission] = useState<"read" | "write">("read");
  const [found, setFound] = useState<Directory | null>(null);
  const searches = useRef(0);
  const action = useAction(onChanged);

  useEffect(() => {
    const text = q.trim().slice(0, 100);
    const seq = ++searches.current;
    if (!text) {
      setFound(null);
      return;
    }
    // Pas zoeken als er even niet getypt wordt
    const timer = setTimeout(() => {
      api
        .directory(text)
        .then((d) => {
          if (seq === searches.current) setFound(d);
        })
        .catch((e: Error) => {
          if (seq === searches.current) action.setError(e.message);
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [q, action.setError]);

  const users = new Set(shares.map((s) => s.user?.id).filter(Boolean));
  const groups = new Set(shares.map((s) => s.group?.id).filter(Boolean));
  const suggestions = {
    users: found?.users.filter((u) => !users.has(u.id)) ?? [],
    groups: found?.groups.filter((g) => !groups.has(g.id)) ?? [],
  };
  const nothing = found !== null && suggestions.users.length + suggestions.groups.length === 0;

  async function add(to: { user_id: string } | { group_id: string }) {
    if (await action.run(() => api.share(node.id, to, permission))) {
      setQ("");
      setFound(null);
    }
  }

  return (
    <section className="share-section" aria-label="Personen en groepen">
      <h3>Personen en groepen</h3>
      <div className="share-add">
        <input
          type="search"
          aria-label="Zoek een persoon of groep"
          placeholder="Naam, e-mailadres of groep"
          maxLength={100}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select
          aria-label="Rechten voor wie je toevoegt"
          value={permission}
          onChange={(e) => setPermission(e.target.value as "read" | "write")}
        >
          <option value="read">{PERMISSION_LABELS.read}</option>
          <option value="write">{PERMISSION_LABELS.write}</option>
        </select>
      </div>
      {nothing && <p className="muted">Niemand gevonden.</p>}
      {(suggestions.users.length > 0 || suggestions.groups.length > 0) && (
        <ul className="picker-list share-suggestions" aria-label="Gevonden">
          {suggestions.users.map((u) => (
            <li key={u.id}>
              <button type="button" disabled={action.busy} onClick={() => add({ user_id: u.id })}>
                <span aria-hidden="true">👤 </span>
                {u.display_name} <span className="muted">{u.email}</span>
              </button>
            </li>
          ))}
          {suggestions.groups.map((g) => (
            <li key={g.id}>
              <button type="button" disabled={action.busy} onClick={() => add({ group_id: g.id })}>
                <span aria-hidden="true">👥 </span>
                {g.name} <span className="muted">groep, {memberCount(g.member_count)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {action.error && <p role="alert">{action.error}</p>}
      {shares.length === 0 ? (
        <p className="muted">Nog met niemand gedeeld.</p>
      ) : (
        <ul className="share-list" aria-label="Gedeeld met">
          {shares.map((s) => {
            const name = s.user?.display_name ?? s.group?.name ?? "Onbekend";
            const expiry = expiryLabel(s.expires_at);
            return (
              <li key={s.id}>
                <div className="share-who">
                  <strong>{name}</strong>{" "}
                  <span className="muted">
                    {s.user ? s.user.email : `groep, ${memberCount(s.group?.member_count ?? 0)}`}
                    {expiry && ` · ${expiry}`}
                  </span>
                </div>
                <div className="share-actions">
                  <select
                    aria-label={`Rechten voor ${name}`}
                    value={s.permission}
                    disabled={action.busy}
                    onChange={(e) =>
                      action.run(() => api.changeShare(s.id, { permission: e.target.value as "read" | "write" }))
                    }
                  >
                    <option value="read">{PERMISSION_LABELS.read}</option>
                    <option value="write">{PERMISSION_LABELS.write}</option>
                  </select>
                  <button type="button" disabled={action.busy} onClick={() => action.run(() => api.deleteShare(s.id))}>
                    Verwijderen
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Links({ node, shares, onChanged }: { node: FileNode; shares: Share[]; onChanged: () => Promise<void> }) {
  return (
    <section className="share-section" aria-label="Links">
      <h3>Links</h3>
      <p className="muted">Met een link kan iedereen erbij die hem heeft, zonder account.</p>
      {shares.length > 0 && (
        <ul className="share-list" aria-label="Links">
          {shares.map((s) => (
            <LinkRow key={s.id} share={s} onChanged={onChanged} />
          ))}
        </ul>
      )}
      <NewLinkForm node={node} onChanged={onChanged} />
    </section>
  );
}

function LinkRow({ share, onChanged }: { share: Share; onChanged: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const action = useAction(onChanged);
  const href = linkHref(share, window.location.origin);
  const title = share.link?.label || "Link";

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  async function copy() {
    setHint(null);
    try {
      // Het klembord werkt alleen via https of localhost, niet via http op het LAN-adres
      if (!navigator.clipboard) throw new Error("Geen klembord");
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
      else setHint("De link is geselecteerd; kopieer hem met Ctrl+C (of lang drukken op een telefoon).");
    }
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
        aria-label={`Adres van ${title}`}
        onFocus={(e) => e.target.select()}
      />
      <div className="share-actions">
        <button type="button" onClick={copy}>
          {copied ? "Gekopieerd" : "Link kopiëren"}
        </button>
        <button type="button" aria-expanded={editing} onClick={() => setEditing((v) => !v)}>
          Wijzigen
        </button>
        <button
          type="button"
          disabled={action.busy}
          onClick={() => {
            if (window.confirm(`"${title}" verwijderen? Wie de link heeft, kan er daarna niet meer bij.`))
              action.run(() => api.deleteShare(share.id));
          }}
        >
          Verwijderen
        </button>
      </div>
      {hint && (
        <p className="muted" role="status">
          {hint}
        </p>
      )}
      {action.error && <p role="alert">{action.error}</p>}
      {editing && (
        <LinkFields
          kind={null}
          initial={{
            permission: share.permission === "upload" ? "upload" : "read",
            date: dateInputValue(share.expires_at),
            password: "",
            removePassword: false,
            label: share.link?.label ?? "",
          }}
          hasPassword={share.link?.has_password ?? false}
          submit="Opslaan"
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

function NewLinkForm({ node, onChanged }: { node: FileNode; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const action = useAction(onChanged);
  if (!open)
    return (
      <div className="toolbar">
        <button type="button" onClick={() => setOpen(true)}>
          Nieuwe link
        </button>
      </div>
    );
  return (
    <div className="share-new">
      <h4>Nieuwe link</h4>
      <LinkFields
        kind={node.kind}
        initial={{ permission: "read", date: "", password: "", removePassword: false, label: "" }}
        hasPassword={false}
        submit="Link maken"
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

/** De velden van een link. kind: bij een nieuwe link (dan is de soort te kiezen; uploaden
 * alleen bij een map); null bij wijzigen. */
function LinkFields({
  kind,
  initial,
  hasPassword,
  submit,
  busy,
  onSubmit,
  onCancel,
}: {
  kind: FileNode["kind"] | null;
  initial: LinkForm;
  hasPassword: boolean;
  submit: string;
  busy: boolean;
  onSubmit: (form: LinkForm) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState(initial);
  const set = (change: Partial<LinkForm>) => setForm((f) => ({ ...f, ...change }));

  function send(e: FormEvent) {
    e.preventDefault();
    onSubmit(form);
  }

  return (
    <form className="share-form" onSubmit={send}>
      {kind !== null && (
        <label>
          Soort
          <select value={form.permission} onChange={(e) => set({ permission: e.target.value as "read" | "upload" })}>
            <option value="read">Alleen bekijken en downloaden</option>
            {kind === "dir" && <option value="upload">Alleen uploaden</option>}
          </select>
          <small className="muted">
            {form.permission === "upload"
              ? "Wie de link heeft, kan bestanden in deze map zetten, maar ziet niet wat erin staat."
              : "Wie de link heeft, kan dit bekijken en downloaden, maar niets veranderen."}
          </small>
        </label>
      )}
      {hasPassword && (
        <label className="check">
          <input
            type="checkbox"
            checked={form.removePassword}
            onChange={(e) => set({ removePassword: e.target.checked, password: "" })}
          />
          Wachtwoord weghalen
        </label>
      )}
      {!form.removePassword && (
        <label>
          {hasPassword ? "Nieuw wachtwoord (leeg = niet veranderen)" : "Wachtwoord (optioneel)"}
          <input
            type="password"
            autoComplete="new-password"
            minLength={MIN_LINK_PASSWORD}
            maxLength={MAX_LINK_PASSWORD}
            value={form.password}
            onChange={(e) => set({ password: e.target.value })}
          />
          <small className="muted">Minstens {MIN_LINK_PASSWORD} tekens. Stuur het apart van de link.</small>
        </label>
      )}
      <label>
        Geldig tot en met (optioneel)
        <input type="date" min={today()} value={form.date} onChange={(e) => set({ date: e.target.value })} />
      </label>
      <label>
        Naam voor jezelf (optioneel)
        <input
          maxLength={100}
          placeholder="Bijv. Voor de familie"
          value={form.label}
          onChange={(e) => set({ label: e.target.value })}
        />
      </label>
      <div className="share-actions">
        <button type="button" onClick={onCancel}>
          Annuleren
        </button>
        <button type="submit" className="primary" disabled={busy}>
          {submit}
        </button>
      </div>
    </form>
  );
}
