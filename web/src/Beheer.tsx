// /beheer en zijn tabbladen (alleen beheerders; de schil controleert de rol). Elk tabblad is een
// eigen adres: /beheer, /beheer/gebruikers, /beheer/opslag, /beheer/taken, /beheer/groepen en
// /beheer/beveiliging. Alt+1 … Alt+6 springt naar een tabblad.

import type { JSX } from "react";
import { Jobs } from "./admin/Jobs";
import { Overview } from "./admin/Overview";
import { SecurityTab } from "./admin/SecurityTab";
import { Storage } from "./admin/Storage";
import { Users } from "./admin/Users";
import { BeheerGroups } from "./BeheerGroups";
import { useCurrentUser } from "./currentUser";
import { type MessageKey, t } from "./i18n";
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

export function tabHref(path: string): string {
  return path ? `/beheer/${path}` : "/beheer";
}

/** Het tabblad bij het stuk van het adres; onbekend is het overzicht. */
export function tabOf(path: string | undefined): TabInfo {
  return TABS.find((tab) => tab.path === (path ?? "")) ?? TABS[0];
}

/** De route /beheer/:tab? */
export default function BeheerPage({ params }: RouteProps): JSX.Element | null {
  const user = useCurrentUser();
  const current = tabOf(params.tab);
  useScope("admin");
  useShortcuts(
    TABS.map((tab, i) => ({
      combo: `alt+${i + 1}`,
      label: t("admin.keys.tab", { name: t(tab.label) }),
      run: () => navigate(tabHref(tab.path)),
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
        tabs={TABS.map((tab) => ({ id: tab.id, label: t(tab.label), href: tabHref(tab.path) }))}
      />
      <div className="admin-panel">
        {current.id === "overview" && <Overview />}
        {current.id === "users" && <Users currentUser={user} />}
        {current.id === "storage" && <Storage />}
        {current.id === "jobs" && <Jobs />}
        {current.id === "groups" && <BeheerGroups />}
        {current.id === "security" && <SecurityTab />}
      </div>
    </main>
  );
}
