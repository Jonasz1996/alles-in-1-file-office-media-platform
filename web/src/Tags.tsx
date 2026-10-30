import { type FormEvent, memo, useCallback, useEffect, useState } from "react";
import { api, type FileNode, type Tag, type TagRef } from "./api";
import { collator, errorText, t } from "./i18n";
import { navigate } from "./router";
import { searchHref } from "./searchQuery";
import { TAG_COLORS, tagChip } from "./tagColors";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { confirm, prompt } from "./ui/dialogs";
import "./files.css";

/** De tags van een map of bestand; een klik toont alles met die tag. */
export const TagChips = memo(function TagChips({ tags }: { tags: TagRef[] | undefined }) {
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
            tabIndex={-1}
            title={t("browser.tags.chipTitle", { name: tag.name })}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              navigate(href);
            }}
          >
            {tag.name}
          </a>
        );
      })}
    </span>
  );
});

const byName = (a: Tag, b: Tag) => collator.compare(a.name, b.name);

/** Tags kiezen voor één map of bestand, en tags maken, hernoemen en verwijderen. */
export function TagEditor({ node, onClose, onSaved }: { node: FileNode; onClose: () => void; onSaved: () => void }) {
  const [all, setAll] = useState<Tag[] | null>(null);
  const [chosen, setChosen] = useState(() => new Set((node.tags ?? []).map((tag) => tag.id)));
  const [name, setName] = useState("");
  const [color, setColor] = useState(TAG_COLORS[1].value);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setAll((await api.tags()).sort(byName));
    } catch (e) {
      setError(errorText(e));
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
      setError(errorText(e));
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
    const newName = (
      await prompt({
        title: t("browser.tags.renameTitle"),
        label: t("browser.tags.renameLabel"),
        initial: tag.name,
        confirm: t("browser.tags.rename"),
        validate: (v) => (v.trim() ? null : t("common.dialog.required")),
      })
    )?.trim();
    if (!newName || newName === tag.name) return;
    if (await act(() => api.changeTag(tag.id, { name: newName }))) {
      await load();
      onSaved(); // de naam staat ook in de lijst met bestanden
    }
  }

  async function remove(tag: Tag) {
    const ok = await confirm({
      title: t("browser.tags.deleteTitle", { name: tag.name }),
      message: t("browser.tags.deleteMessage", { n: tag.count }),
      confirm: t("browser.tags.delete"),
      danger: true,
    });
    if (!ok) return;
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
    <Dialog
      title={t("browser.tags.title", { name: node.name })}
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" disabled={busy || all === null} onClick={save}>
            {t("common.save")}
          </Button>
        </>
      }
    >
      {all === null && !error && <p className="muted">{t("common.loading")}</p>}
      {all?.length === 0 && <p className="muted">{t("browser.tags.none")}</p>}
      {all && all.length > 0 && (
        <ul className="tag-list">
          {all.map((tag, i) => (
            <li key={tag.id}>
              <label>
                <input
                  type="checkbox"
                  data-autofocus={i === 0 ? "" : undefined}
                  checked={chosen.has(tag.id)}
                  onChange={(e) => toggle(tag.id, e.target.checked)}
                />
                <span {...tagChip(tag.color)}>{tag.name}</span>
              </label>
              <span className="muted">{t("browser.tags.count", { n: tag.count })}</span>
              <Button variant="link" disabled={busy} onClick={() => rename(tag)}>
                {t("browser.tags.rename")}
              </Button>
              <Button variant="link" disabled={busy} onClick={() => remove(tag)}>
                {t("browser.tags.delete")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form className="inline-form" onSubmit={create}>
        <input
          aria-label={t("browser.tags.newName")}
          placeholder={t("browser.tags.newPlaceholder")}
          maxLength={50}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select aria-label={t("browser.tags.color")} value={color} onChange={(e) => setColor(e.target.value)}>
          {TAG_COLORS.map((c) => (
            <option key={c.value} value={c.value}>
              {t(c.label)}
            </option>
          ))}
        </select>
        <Button type="submit" disabled={busy || !name.trim()}>
          {t("browser.tags.add")}
        </Button>
      </form>
      {error && <p role="alert">{error}</p>}
    </Dialog>
  );
}
