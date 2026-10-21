// De schil van de app: inloggen, de balk bovenaan met het accountmenu, de routetabel en wat
// altijd klaarstaat (dialogen, meldingen, sneltoetsen, uploads). Pagina's laden lazy.

import {
  Component,
  type ErrorInfo,
  type JSX,
  type ReactNode,
  Suspense,
  use,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { ApiError, api, type User } from "./api";
import { CurrentUser, ShellActionsContext } from "./currentUser";
import { liveEvents } from "./events";
import { applyAccountLocale, errorText, type Locale, lazyWithAreas, locale, setLocale, t } from "./i18n";
import { Link } from "./links";
import { Login } from "./Login";
import { markReady } from "./perf";
import { AppStatus } from "./pwa/AppStatus";
import {
  type NavSection,
  navigate,
  navigationKind,
  type RouteDef,
  useLocation,
  useNavSectionOverride,
  useRestoreScroll,
} from "./router";
import { allowed, isPublicPath, publicRoutes, type Resolved, resolve } from "./routes";
import { NoAccess } from "./shellPages";
import { DialogHost } from "./ui/dialogs";
import type { MenuItem } from "./ui/menu";
import { MenuButton } from "./ui/Menu";
import { focusPage, requestPageFocus } from "./ui/PageTitle";
import { ShortcutHelp, openShortcutHelp } from "./ui/ShortcutHelp";
import { OfflineScreen } from "./ui/OfflineScreen";
import { setTheme, type Theme, useTheme } from "./ui/theme";
import { announce, ToastRegion } from "./ui/toast";
import { UploadTray } from "./upload/index";
import { uploadQueue } from "./uploadQueue";

// --- pagina's laden ---

type PageModule = Awaited<ReturnType<RouteDef["load"]>>;
const pages = new Map<RouteDef, Promise<PageModule>>();

/** Het chunk (en de woordenboeken) van een route; één keer per route, ook bij vooraf laden. */
function loadPage(def: RouteDef): Promise<PageModule> {
  let p = pages.get(def);
  if (!p) {
    p = def.load();
    pages.set(def, p);
  }
  return p;
}

/** De pagina zelf. Hetzelfde onderdeel (Bestanden op / en op /map/:id) blijft staan bij een
 * andere route, net als vroeger. */
function RoutePage({ route }: { route: Resolved }): JSX.Element {
  const Page = use(loadPage(route.def)).default;
  return <Page params={route.params} />;
}

const CHUNK_RELOAD = "platform-chunk-reload";

/** Lukte het laden van een stuk van de app niet (nieuwe versie uitgerold, oud chunk weg)? */
function isChunkError(e: unknown): boolean {
  const msg = e instanceof Error ? `${e.name} ${e.message}` : String(e);
  return /dynamically imported module|module script failed|Importing a module|ChunkLoadError|Unable to preload/i.test(msg);
}

/** Eén keer herladen per 10 s: daarna de foutpagina, anders blijft hij herladen. */
function reloadOnce(): boolean {
  try {
    const last = Number(sessionStorage.getItem(CHUNK_RELOAD) ?? 0);
    if (Date.now() - last < 10_000) return false;
    sessionStorage.setItem(CHUNK_RELOAD, String(Date.now()));
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}

class PageBoundary extends Component<{ path: string; children: ReactNode }, { error: unknown; path: string }> {
  state = { error: null as unknown, path: this.props.path };

  static getDerivedStateFromError(error: unknown) {
    return { error };
  }

  static getDerivedStateFromProps(
    props: { path: string },
    state: { error: unknown; path: string },
  ): { error: unknown; path: string } | null {
    // Naar een andere pagina: opnieuw proberen
    return props.path !== state.path ? { error: null, path: props.path } : null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // Een mislukte lading niet onthouden: bij de volgende navigatie opnieuw proberen
    pages.clear();
    if (isChunkError(error) && reloadOnce()) return;
    console.error(error, info.componentStack);
  }

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <main>
        <h1 data-page-title="" tabIndex={-1}>
          {t("shell.chunkError.title")}
        </h1>
        {!isChunkError(this.state.error) && <p role="alert">{errorText(this.state.error)}</p>}
        <button type="button" className="primary" onClick={() => window.location.reload()}>
          {t("shell.chunkError.reload")}
        </button>
      </main>
    );
  }
}

/** Na elke navigatie (een ander pad): titel, focus naar de kop en een aankondiging. Staat in
 * dezelfde Suspense als de pagina, dus loopt pas als die er staat. */
function AfterNavigation({ route, path }: { route: Resolved; path: string }): null {
  const shown = useRef<string | null>(null);
  const title = t(route.def.title);
  useRestoreScroll(true);
  // Alleen bij een ander pad (niet bij ?bestand= of een filter)
  useEffect(() => {
    if (shown.current === path) return; // StrictMode draait het effect twee keer
    const first = shown.current === null;
    shown.current = path;
    document.title = t("shell.pageTitle", { title });
    if (first) {
      // De eerste pagina: geen focus verplaatsen of aankondigen (de browser doet dat al)
      markReady("route");
      return;
    }
    if (navigationKind() !== "replace") {
      requestPageFocus();
      focusPage();
    }
    announce(t("shell.navigated", { title }));
  }, [path, title]);
  return null;
}

function Loading(): JSX.Element {
  return <main className="center">{t("common.loading")}</main>;
}

/** Wat altijd klaarstaat, ook op een publieke link. */
function Overlays(): JSX.Element {
  return (
    <>
      <DialogHost />
      <ToastRegion />
      <ShortcutHelp />
      <AppStatus />
    </>
  );
}

function PageArea({ route, path }: { route: Resolved; path: string }): JSX.Element {
  return (
    <div id="inhoud" tabIndex={-1}>
      <PageBoundary path={path}>
        <Suspense fallback={<Loading />}>
          <AfterNavigation route={route} path={path} />
          <RoutePage route={route} />
        </Suspense>
      </PageBoundary>
    </div>
  );
}

// --- de app ---

export function App(): JSX.Element {
  const { path } = useLocation();
  // Een publieke link werkt zonder in te loggen: dan niets van de login (geen refresh, geen
  // live stroom), ook niet als je toevallig ook ingelogd bent
  if (isPublicPath(path)) {
    return (
      <>
        <PageArea route={resolve(path, publicRoutes)} path={path} />
        <Overlays />
      </>
    );
  }
  return <LoggedIn path={path} />;
}

/** Bij het opstarten: het chunk van de eerste pagina laden terwijl /auth/refresh loopt. */
function prefetch(path: string): void {
  const route = resolve(path);
  if (route.def.pattern !== "/" && route.def.pattern !== "/map/:id") return;
  loadPage(route.def).catch(() => {}); // de fout komt straks bij het tonen
  route.def.preload?.(route.params);
}

type Boot = { state: "checking" } | { state: "offline" } | { state: "done"; message: string | null };

/** Geen verbinding (fetch gooit) of de server zelf is er niet (502/503/504 van de proxy). */
function isOffline(e: unknown): boolean {
  return e instanceof TypeError || (e instanceof ApiError && e.status >= 502 && e.status <= 504);
}

function LoggedIn({ path }: { path: string }): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [boot, setBoot] = useState<Boot>({ state: "checking" });
  // Nieuwe herstelcodes blijven staan tot "Ik heb ze bewaard", ook als intussen de
  // gebruiker ververst is en de rest van de app verschijnt.
  const [codes, setCodes] = useState<string[] | null>(null);

  const reloadUser = useCallback(() => {
    api
      .me()
      .then((u) => {
        if (api.loggedIn) setUser(u); // niet terug als er intussen is uitgelogd
      })
      .catch(() => {});
  }, []);

  const login = useCallback((u: User) => {
    if (applyAccountLocale(u.locale)) return; // de pagina herlaadt in de taal van het account
    setUser(u);
  }, []);

  const check = useCallback(() => {
    setBoot({ state: "checking" });
    api
      .refresh()
      .then((u) => {
        const failed = api.lastRefresh.error ?? null;
        if (!u && isOffline(failed)) return setBoot({ state: "offline" });
        // Bijv. 403 account_disabled: terug naar inloggen, met de reden
        const message = !u && failed && failed.status === 403 ? errorText(failed) : null;
        if (u) login(u);
        setBoot({ state: "done", message });
      })
      .catch((e: unknown) => {
        if (isOffline(e)) setBoot({ state: "offline" });
        else setBoot({ state: "done", message: errorText(e) });
      });
  }, [login]);

  useEffect(() => {
    api.onLoggedOut = () => {
      uploadQueue.stopAll();
      liveEvents.stop();
      setCodes(null);
      setUser(null);
    };
    api.onSetupRequired = reloadUser;
    prefetch(window.location.pathname);
    check();
  }, [reloadUser, check]);

  // Live gebeurtenissen zolang er iemand ingelogd is (en niet eerst 2FA moet aanzetten)
  const live = user !== null && !user.totp_setup_required;
  useEffect(() => {
    if (!live) return;
    liveEvents.start();
    return () => liveEvents.stop();
  }, [live]);

  const actions = useMemo(
    () => ({
      reloadUser,
      showRecoveryCodes: (newCodes: string[]) => {
        setCodes(newCodes);
        reloadUser(); // 2FA staat nu aan: meteen overal zo tonen
      },
    }),
    [reloadUser],
  );

  const guest = user?.role === "guest";
  // Gasten hebben geen eigen bestanden: op / zien ze wat met hen gedeeld is
  const redirect = user !== null && guest && path === "/" && !user.totp_setup_required;
  useLayoutEffect(() => {
    if (redirect) navigate("/gedeeld", { replace: true });
  }, [redirect]);

  if (boot.state === "offline") return <OfflineScreen onRetry={check} />;
  if (boot.state === "checking") return <Loading />;
  if (!user)
    return (
      <>
        <Login onLoggedIn={login} message={boot.message} />
        <Overlays />
      </>
    );

  async function logout() {
    uploadQueue.stopAll(); // de uploads blijven op de server staan en gaan later verder
    liveEvents.stop();
    await api.logout();
    setCodes(null);
    setUser(null);
    navigate("/");
  }

  // 2FA verplicht en nog niet aan: alleen 2FA aanzetten en uitloggen.
  const setupRequired = user.totp_setup_required;
  const route = setupRequired ? resolve("/account") : resolve(path);

  let page: ReactNode;
  if (codes) {
    page = (
      <div id="inhoud" tabIndex={-1}>
        <Suspense fallback={<Loading />}>
          <main>
            <h1>{t("shell.recoveryCodes")}</h1>
            <RecoveryCodes
              codes={codes}
              onDone={() => {
                setCodes(null);
                reloadUser();
              }}
            />
          </main>
        </Suspense>
      </div>
    );
  } else if (redirect) page = null;
  else if (!allowed(route.def, user.role))
    page = (
      <div id="inhoud" tabIndex={-1}>
        <NoAccess />
      </div>
    );
  else page = <PageArea route={route} path={setupRequired ? "/account" : path} />;

  return (
    <CurrentUser.Provider value={user}>
      <ShellActionsContext.Provider value={actions}>
        <a className="skip-link" href="#inhoud">
          {t("shell.skip")}
        </a>
        <TopBar user={user} section={route.def.section} setupRequired={setupRequired} onLogout={logout} />
        {page}
        <UploadSlot />
        <Overlays />
      </ShellActionsContext.Provider>
    </CurrentUser.Provider>
  );
}

const RecoveryCodes = lazyWithAreas<{ codes: string[]; onDone: () => void }>(
  () => import("./Account").then((m) => ({ default: m.RecoveryCodes })),
  ["account"],
);

/** De uploadlade, alleen als er iets in de wachtrij staat (anders hoeft zijn chunk niet te laden). */
function UploadSlot(): JSX.Element | null {
  const busy = useSyncExternalStore(uploadQueue.subscribe, () => uploadQueue.getSnapshot().length > 0);
  if (!busy) return null;
  return (
    <Suspense fallback={null}>
      <UploadTray />
    </Suspense>
  );
}

// --- de balk bovenaan ---

function TopBar({
  user,
  section,
  setupRequired,
  onLogout,
}: {
  user: User;
  section: NavSection | null;
  setupRequired: boolean;
  onLogout: () => void;
}): JSX.Element {
  const override = useNavSectionOverride();
  const theme = useTheme();
  const current = override ?? section;
  const guest = user.role === "guest";

  const link = (to: string, label: string, s: NavSection) => (
    <Link to={to} aria-current={current === s ? "page" : undefined}>
      {label}
    </Link>
  );

  const themes: { value: Theme; label: string }[] = [
    { value: "light", label: t("shell.theme.light") },
    { value: "dark", label: t("shell.theme.dark") },
    { value: "system", label: t("shell.theme.system") },
  ];
  const languages: { value: Locale; label: string }[] = [
    { value: "nl", label: t("shell.language.nl") },
    { value: "en", label: t("shell.language.en") },
  ];

  const items: MenuItem[] = [
    ...(setupRequired
      ? []
      : [{ id: "account", label: t("shell.account.account"), icon: "user" as const, onSelect: () => navigate("/account") }]),
    {
      id: "theme",
      label: t("shell.account.theme"),
      icon: theme === "dark" ? "moon" : "sun",
      items: themes.map((o) => ({
        id: `theme-${o.value}`,
        label: o.label,
        checked: theme === o.value,
        onSelect: () => setTheme(o.value),
      })),
    },
    {
      id: "language",
      label: t("shell.account.language"),
      icon: "language",
      items: languages.map((o) => ({
        id: `language-${o.value}`,
        label: o.label,
        checked: locale() === o.value,
        onSelect: () => {
          if (o.value !== locale()) void setLocale(o.value, { account: true });
        },
      })),
    },
    {
      id: "shortcuts",
      label: t("shell.account.shortcuts"),
      icon: "keyboard",
      shortcut: "?",
      onSelect: openShortcutHelp,
    },
    { id: "logout", label: t("shell.account.logout"), icon: "logout", separatorBefore: true, onSelect: onLogout },
  ];

  return (
    <header className="topbar">
      <strong className="brand">{t("shell.brand")}</strong>
      {!setupRequired && (
        <nav aria-label={t("shell.nav.label")}>
          {!guest && link("/", t("shell.nav.files"), "files")}
          {!guest && link("/fotos", t("shell.nav.photos"), "photos")}
          {link("/gedeeld", t("shell.nav.shared"), "shared")}
          {!guest && link("/prullenbak", t("shell.nav.trash"), "trash")}
          {user.role === "admin" && link("/beheer", t("shell.nav.admin"), "admin")}
        </nav>
      )}
      <span className="spacer" />
      <MenuButton
        items={items}
        label={t("shell.account.menu", { name: user.display_name })}
        className={current === "account" ? "current" : undefined}
      >
        {user.display_name}
      </MenuButton>
    </header>
  );
}
