// "N bestanden bestaan al": één keer per batch, vóór er een upload bestaat. Vervangen (de
// vorige wordt een versie), Beide houden of Overslaan; met "Voor allemaal" geldt de keuze
// voor de rest, anders vraagt hij ze één voor één. Sluiten (Esc) uploadt niets van de batch.

import { type JSX, useId, useRef, useState } from "react";
import { t } from "../i18n";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useScope, useShortcuts } from "../ui/keys";
import type { ConflictAnswer, ConflictItem } from "./conflicts";
import type { ConflictChoice } from "./plan";

export interface ConflictShortcuts {
  replace: string;
  keep: string;
  skip: string;
}

export function ConflictDialog({
  items,
  shortcuts,
  onAnswer,
}: {
  items: readonly ConflictItem[];
  /** De letters voor de drie keuzes (per taal) */
  shortcuts: ConflictShortcuts;
  onAnswer: (answer: ConflictAnswer) => void;
}): JSX.Element {
  const [index, setIndex] = useState(0);
  const [forAll, setForAll] = useState(false);
  const chosen = useRef<ConflictChoice[]>([]);
  const id = useId();
  const current = items[index];
  const many = items.length > 1;
  const rest = items.length - index;

  function choose(choice: ConflictChoice) {
    const answers = chosen.current;
    if (forAll || !many) {
      for (let i = index; i < items.length; i++) answers[i] = choice;
      onAnswer([...answers]);
      return;
    }
    answers[index] = choice;
    if (index + 1 >= items.length) onAnswer([...answers]);
    else setIndex(index + 1);
  }

  // Boven de scope van de dialoog zelf (Dialog zet "dialog" in een layout-effect)
  useScope("upload-conflict");
  useShortcuts(
    [
      { combo: shortcuts.replace, label: t("upload.conflict.shortcuts.replace"), run: () => choose("replace") },
      { combo: shortcuts.keep, label: t("upload.conflict.shortcuts.keep"), run: () => choose("keep") },
      { combo: shortcuts.skip, label: t("upload.conflict.shortcuts.skip"), run: () => choose("skip") },
    ],
    { scope: "upload-conflict" },
  );

  const option = (choice: ConflictChoice, label: string, hint: string, key: string, autofocus = false) => (
    <div className="conflict-option">
      <Button
        variant={choice === "keep" ? "primary" : "default"}
        aria-describedby={`${id}-${choice}`}
        aria-keyshortcuts={key.toUpperCase()}
        data-autofocus={autofocus || undefined}
        onClick={() => choose(choice)}
      >
        {label}
      </Button>
      <span id={`${id}-${choice}`} className="muted">
        {hint}
      </span>
    </div>
  );

  return (
    <Dialog title={t("upload.conflict.title", { n: items.length })} onClose={() => onAnswer(null)} size="s">
      <div className="conflict-body">
        <p>{t("upload.conflict.intro")}</p>
        <p className="conflict-name">
          <strong>{current?.path}</strong>
          {many && <span className="muted"> {t("upload.conflict.position", { i: index + 1, n: items.length })}</span>}
        </p>
        {current?.kind === "dir" && <p className="muted">{t("upload.conflict.folderNote")}</p>}
        {many && (
          <label className="conflict-all">
            <input type="checkbox" checked={forAll} onChange={(e) => setForAll(e.currentTarget.checked)} />
            {t("upload.conflict.forAll", { n: rest })}
          </label>
        )}
        <div className="conflict-options">
          {option("replace", t("upload.conflict.replace"), t("upload.conflict.replaceHint"), shortcuts.replace)}
          {option("keep", t("upload.conflict.keep"), t("upload.conflict.keepHint"), shortcuts.keep, true)}
          {option("skip", t("upload.conflict.skip"), t("upload.conflict.skipHint"), shortcuts.skip)}
        </div>
      </div>
    </Dialog>
  );
}
