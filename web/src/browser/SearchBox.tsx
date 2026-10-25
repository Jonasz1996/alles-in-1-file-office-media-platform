// Het zoekveld boven in de bestandenlijst; de resultaten staan op /zoeken (Search.tsx, dat
// SearchBox ook exporteert). Een eigen module, zodat Bestanden de zoekpagina niet meelaadt.

import { type JSX, useState } from "react";
import { isGuest, useCurrentUser } from "../currentUser";
import { t } from "../i18n";
import { navigate } from "../router";
import { searchHref } from "../searchQuery";
import { Button } from "../ui/Button";

/** Het id van het zoekveld boven in Bestanden (de sneltoets / zet de focus erin). */
export const SEARCH_INPUT_ID = "zoekveld";

/** Zoekveld boven in de bestandenlijst; de resultaten staan op /zoeken. */
export function SearchBox(): JSX.Element {
  const [q, setQ] = useState("");
  // Gasten hebben geen eigen bestanden; zoeken gaat dan door wat met hen gedeeld is
  const label = isGuest(useCurrentUser()) ? t("browser.search.boxGuest") : t("browser.search.box");
  return (
    <form
      role="search"
      className="search-box"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) navigate(searchHref({ q }));
      }}
    >
      <input
        id={SEARCH_INPUT_ID}
        type="search"
        aria-label={label}
        placeholder={label}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <Button type="submit">{t("browser.search.submit")}</Button>
    </form>
  );
}

