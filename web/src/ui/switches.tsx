// Thema en taal kiezen (in het accountmenu staan ze als submenu; deze vorm is voor pagina's
// zoals Account).

import { type JSX, useId, useState } from "react";
import { type Locale, locale, setLocale, t } from "../i18n";
import { setTheme, type Theme, useTheme } from "./theme";

/** Licht, donker of het systeem volgen; geldt meteen, op dit apparaat. */
export function ThemeSwitch(): JSX.Element {
  const theme = useTheme();
  const name = useId();
  const options: { value: Theme; label: string }[] = [
    { value: "light", label: t("shell.theme.light") },
    { value: "dark", label: t("shell.theme.dark") },
    { value: "system", label: t("shell.theme.system") },
  ];
  return (
    <fieldset className="switch">
      <legend>{t("shell.theme.label")}</legend>
      {options.map((o) => (
        <label key={o.value} className="check">
          <input
            type="radio"
            name={name}
            value={o.value}
            checked={theme === o.value}
            onChange={() => setTheme(o.value)}
          />
          {o.label}
        </label>
      ))}
    </fieldset>
  );
}

/** Nederlands of Engels; bewaart het ook bij het account (account: true) en herlaadt. Pas met de
 * knop: met de pijltjes door een dichte keuzelijst gaan geeft op Windows en Linux meteen een
 * change, en dan zou de pagina al bij de eerste toets herladen (WCAG 3.2.2). */
export function LanguageSwitch({ account = true }: { account?: boolean }): JSX.Element {
  const id = useId();
  const [choice, setChoice] = useState<Locale>(locale);
  const options: { value: Locale; label: string }[] = [
    { value: "nl", label: t("shell.language.nl") },
    { value: "en", label: t("shell.language.en") },
  ];
  return (
    <form
      className="switch"
      onSubmit={(e) => {
        e.preventDefault();
        if (choice !== locale()) void setLocale(choice, { account });
      }}
    >
      <label htmlFor={id}>{t("shell.language.label")}</label>{" "}
      <select id={id} value={choice} onChange={(e) => setChoice(e.target.value as Locale)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} lang={o.value}>
            {o.label}
          </option>
        ))}
      </select>{" "}
      <button type="submit" disabled={choice === locale()}>
        {t("shell.language.apply")}
      </button>
    </form>
  );
}
