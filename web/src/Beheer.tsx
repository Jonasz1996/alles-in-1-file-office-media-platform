// /beheer en zijn tabbladen (alleen beheerders; de schil controleert de rol). Elk tabblad is een
// eigen adres: /beheer, /beheer/gebruikers, /beheer/opslag, /beheer/taken, /beheer/groepen en
// /beheer/beveiliging. Alt+1 … Alt+6 springt naar een tabblad.
//
// Fase 4: het zevende tabblad Office (Alt+7), alleen met office ingesteld. Fase 5 (M0): het
// achtste tabblad Media (Alt+8), alleen als GET /api/v1/admin/media antwoordt; de inhoud is van
// M10 (admin/MediaTab.tsx, een eigen chunk). Alt+8 blijft Alt+8, ook zonder office.

import { type JSX, lazy, Suspense } from "react";
import { Jobs } from "./admin/Jobs";
import { OfficeTab } from "./admin/OfficeTab";
import { Overview } from "./admin/Overview";
import { SecurityTab } from "./admin/SecurityTab";
import { Storage } from "./admin/Storage";
import { Users } from "./admin/Users";
import { BeheerGroups } from "./BeheerGroups";
import { useCurrentUser } from "./currentUser";
import { type MessageKey, t } from "./i18n";
import { useStatus } from "./media";
import { adminMediaStatus } from "./media/api";
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

/** Het achtste tabblad (fase 5), alleen als de server het overzicht van media geeft. */
const MEDIA_TAB: TabInfo = { id: "media", path: "media", label: "admin.media.tab" };

/** De tabbladen bij deze status: zonder office en media (of als de server het niet zegt) die van
 * fase 3. */
export function tabsFor(status: Pick<OfficeStatus, "state"> | null, media = false): readonly TabInfo[] {
  const tabs = status && status.state !== "off" ? [...TABS, OFFICE_TAB] : [...TABS];
  if (media) tabs.push(MEDIA_TAB);
  return tabs;
}

/** De sneltoetsen: Alt+1 … Alt+8, elk vast bij zijn tabblad (Alt+8 is Media, ook zonder Office). */
export function tabKeys(): readonly { combo: string; tab: TabInfo }[] {
  return [...TABS, OFFICE_TAB, MEDIA_TAB].map((tab, i) => ({ combo: `alt+${i + 1}`, tab }));
}

const MediaTab = lazy(() => import("./admin/MediaTab"));

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
  const media = useStatus(adminMediaStatus) !== null;
  const tabs = tabsFor(useOfficeStatus(), media);
  const current = tabOf(params.tab, tabs);
  useScope("admin");
  useShortcuts(
    tabKeys().map(({ combo, tab }) => ({
      combo,
      label: t("admin.keys.tab", { name: t(tab.label) }),
      run: () => navigate(tabHref(tab.path)),
      // Alt+7 en Alt+8 alleen met hun tabblad; anders vangt hij de toets niet
      when: tab === OFFICE_TAB || tab === MEDIA_TAB ? () => tabs.includes(tab) : undefined,
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
        {current.id === "media" && (
          <Suspense fallback={<p>{t("common.loading")}</p>}>
            <MediaTab />
          </Suspense>
        )}
      </div>
    </main>
  );
}
