import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, type FileNode, type Tag, type TagRef } from "./api";
import { Dialog } from "./Dialog";
import { navigate } from "./router";
import { searchHref } from "./searchQuery";
import { TAG_COLORS, tagChip } from "./tagColors";
import "./files.css";

/** De tags van een map of bestand; een klik toont alles met die tag. */
export function TagChips({ tags }: { tags: TagRef[] | undefined }) {
  if (!tags?.length) return null;
  return (
    <span className="tags">
      {tags.map((tag) => {
        const href = searchHref({ tag: tag.id });
        return (
          <a
            key={tag.id}
            {...tagChip(tag.color)}
            href={href}
            title={`Alles met de tag "${tag.name}"`}
            onClick={(e) => {
              e.preventDefault();
              navigate(href);
            }}
          >
            {tag.name}
          </a>
        );
      })}
    </span>
  );
}

const byName = (a: Tag, b: Tag) => a.name.localeCompare(b.name, "nl", { sensitivity: "base" });

/** Tags kiezen voor één map of bestand, en tags maken, hernoemen en verwijderen. */
export function TagEditor({ node, onClose, onSaved }: { node: FileNode; onClose: () => void; onSaved: () => void }) {
  const [all, setAll] = useState<Tag[] | null>(null);
  const [chosen, setChosen] = useState(() => new Set((node.tags ?? []).map((t) => t.id)));
  const [name, setName] = useState("");
  const [color, setColor] = useState(TAG_COLORS[1].value);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setAll((await api.tags()).sort(byName));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function act(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    try {
      await fn();
      setError(null);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string, on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    await act(async () => {
      const tag = await api.createTag(name.trim(), color);
      setAll((prev) => [...(prev ?? []), tag].sort(byName));
      toggle(tag.id, true);
      setName("");
    });
  }

  async function rename(tag: Tag) {
    const newName = window.prompt("Nieuwe naam voor de tag", tag.name)?.trim();
    if (!newName || newName === tag.name) return;
    if (await act(() => api.changeTag(tag.id, { name: newName }))) {
      await load();
      onSaved(); // de naam staat ook in de lijst met bestanden
    }
  }

  async function remove(tag: Tag) {
    const where = tag.count === 1 ? "1 map of bestand" : `${tag.count} mappen en bestanden`;
    if (!window.confirm(`Tag "${tag.name}" verwijderen? Hij verdwijnt van ${where}.`)) return;
    if (await act(() => api.deleteTag(tag.id))) {
      toggle(tag.id, false);
      await load();
      onSaved();
    }
  }

  async function save() {
    if (await act(() => api.setTags(node.id, [...chosen]))) {
      onSaved();
      onClose();
    }
  }

  return (
    <Dialog title={`Tags voor "${node.name}"`} onClose={onClose}>
      {all === null && !error && <p className="muted">Laden…</p>}
      {all?.length === 0 && <p className="muted">Je hebt nog geen tags. Maak er hieronder een.</p>}
      {all && all.length > 0 && (
        <ul className="tag-list">
          {all.map((tag) => (
            <li key={tag.id}>
              <label>
                <input
                  type="checkbox"
                  checked={chosen.has(tag.id)}
                  onChange={(e) => toggle(tag.id, e.target.checked)}
                />
                <span {...tagChip(tag.color)}>
                  {tag.name}
                </span>
              </label>
              <span className="muted">{tag.count}×</span>
              <button type="button" className="link" disabled={busy} onClick={() => rename(tag)}>
                Hernoemen
              </button>
              <button type="button" className="link" disabled={busy} onClick={() => remove(tag)}>
                Verwijderen
              </button>
            </li>
          ))}
        </ul>
      )}
      <form className="inline-form" onSubmit={create}>
        <input
          aria-label="Naam van een nieuwe tag"
          placeholder="Nieuwe tag"
          maxLength={50}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select aria-label="Kleur" value={color} onChange={(e) => setColor(e.target.value)}>
          {TAG_COLORS.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <button type="submit" disabled={busy || !name.trim()}>
          Toevoegen
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Annuleren
        </button>
        <button type="button" className="primary" disabled={busy || all === null} onClick={save}>
          Opslaan
        </button>
      </div>
    </Dialog>
  );
}
