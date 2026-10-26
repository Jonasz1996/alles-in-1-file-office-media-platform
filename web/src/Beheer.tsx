// /beheer en zijn tabbladen (alleen beheerders; de schil controleert de rol). Elk tabblad is een
// eigen adres: /beheer, /beheer/gebruikers, /beheer/opslag, /beheer/taken, /beheer/groepen en
// /beheer/beveiliging. Alt+1 … Alt+6 springt naar een tabblad.

import type { JSX } from "react";
import { Jobs } from "./admin/Jobs";
import { OfficeTab } from "./admin/OfficeTab";
import { Overview } from "./admin/Overview";
import { SecurityTab } from "./admin/SecurityTab";
import { Storage } from "./admin/Storage";
import { Users } from "./admin/Users";
import { BeheerGroups } from "./BeheerGroups";
import { useCurrentUser } from "./currentUser";
import { type MessageKey, t } from "./i18n";
import { useOfficeStatus } from "./office";
import type { OfficeStatus } from "./office/types";
import { navigate, type RouteProps } from "./router";
import { useScope, useShortcuts } from "./ui/keys";
import { PageTitle } from "./ui/PageTitle";
import { Tabs } from "./ui/Tabs";
import "./admin/admin.css";

interface TabInfo {
  id: string;
  /** Het stuk na /beheer/ ("" = het overzicht) */
  path: string;
  label: MessageKey;
}

const TABS: readonly TabInfo[] = [
  { id: "overview", path: "", label: "admin.tabs.overview" },
  { id: "users", path: "gebruikers", label: "admin.tabs.users" },
  { id: "storage", path: "opslag", label: "admin.tabs.storage" },
  { id: "jobs", path: "taken", label: "admin.tabs.jobs" },
  { id: "groups", path: "groepen", label: "admin.tabs.groups" },
  { id: "security", path: "beveiliging", label: "admin.tabs.security" },
];

/** Het zevende tabblad (fase 4), alleen met office ingesteld. */
const OFFICE_TAB: TabInfo = { id: "office", path: "office", label: "admin.office.tab" };

/** De tabbladen bij deze status: zonder office (of als de server het niet zegt) die van fase 3. */
export function tabsFor(status: Pick<OfficeStatus, "state"> | null): readonly TabInfo[] {
  return status && status.state !== "off" ? [...TABS, OFFICE_TAB] : TABS;
}

export function tabHref(path: string): string {
  return path ? `/beheer/${path}` : "/beheer";
}

/** Het tabblad bij het stuk van het adres; onbekend is het overzicht. */
export function tabOf(path: string | undefined, tabs: readonly TabInfo[] = TABS): TabInfo {
  return tabs.find((tab) => tab.path === (path ?? "")) ?? tabs[0];
}

/** De route /beheer/:tab? */
export default function BeheerPage({ params }: RouteProps): JSX.Element | null {
  const user = useCurrentUser();
  const tabs = tabsFor(useOfficeStatus());
  const current = tabOf(params.tab, tabs);
  const hasOffice = tabs.length > TABS.length;
  useScope("admin");
  useShortcuts(
    [...TABS, OFFICE_TAB].map((tab, i) => ({
      combo: `alt+${i + 1}`,
      label: t("admin.keys.tab", { name: t(tab.label) }),
      run: () => navigate(tabHref(tab.path)),
      // Alt+7 alleen met het tabblad Office; anders vangt hij de toets niet
      when: tab === OFFICE_TAB ? () => hasOffice : undefined,
    })),
    { scope: "admin" },
  );
  if (!user) return null;

  return (
    <main className="wide admin">
      <PageTitle>{t("admin.title")}</PageTitle>
      <Tabs
        label={t("admin.tabs.label")}
        current={current.id}
        tabs={tabs.map((tab) => ({ id: tab.id, label: t(tab.label), href: tabHref(tab.path) }))}
      />
      <div className="admin-panel">
        {current.id === "overview" && <Overview />}
        {current.id === "users" && <Users currentUser={user} />}
        {current.id === "storage" && <Storage />}
        {current.id === "jobs" && <Jobs />}
        {current.id === "groups" && <BeheerGroups />}
        {current.id === "security" && <SecurityTab />}
        {current.id === "office" && <OfficeTab />}
      </div>
    </main>
  );
}
