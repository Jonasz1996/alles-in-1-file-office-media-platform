// De twee tabbladen van /gedeeld: met mij en door mij (elk een eigen adres). Gasten zien ze
// niet: die delen zelf niets.

import type { JSX } from "react";
import { t } from "../i18n";
import { Tabs } from "../ui/Tabs";

export function SharedTabs({ current }: { current: "withMe" | "byMe" }): JSX.Element {
  return (
    <Tabs
      label={t("sharing.tabs")}
      current={current}
      tabs={[
        { id: "withMe", label: t("sharing.sharedWithMe"), href: "/gedeeld" },
        { id: "byMe", label: t("sharing.myShares"), href: "/gedeeld/door-mij" },
      ]}
    />
  );
}
