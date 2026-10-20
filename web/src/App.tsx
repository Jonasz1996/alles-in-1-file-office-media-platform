import { useCallback, useEffect, useState } from "react";
import { Account, RecoveryCodes } from "./Account";
import { api, type User } from "./api";
import { Beheer } from "./Beheer";
import { CurrentUser } from "./currentUser";
import { liveEvents } from "./events";
import { Files } from "./Files";
import { Login } from "./Login";
import { Public } from "./Public";
import { linkToken } from "./publicApi";
import { navigate, usePath } from "./router";
import { Search } from "./Search";
import { SharedWithMe } from "./SharedWithMe";
import { Trash } from "./Trash";
import { uploadQueue } from "./uploadQueue";

export function App() {
  const path = usePath();
  // Een publieke link werkt zonder in te loggen: dan niets van de login (geen refresh, geen
  // live stroom), ook niet als je toevallig ook ingelogd bent
  if (path.startsWith("/s/")) {
    const token = linkToken(path);
    return <Public key={token ?? ""} token={token} />;
  }
  return <LoggedIn path={path} />;
}

function LoggedIn({ path }: { path: string }) {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  // Nieuwe herstelcodes blijven staan tot "Ik heb ze bewaard", ook als intussen de
  // gebruiker ververst is en de rest van de app verschijnt.
  const [codes, setCodes] = useState<string[] | null>(null);
  // De map die open staat is met je gedeeld (voor de navigatie)
  const [folderShared, setFolderShared] = useState(false);

  const reloadUser = useCallback(() => {
    api
      .me()
      .then((u) => {
        if (api.loggedIn) setUser(u); // niet terug als er intussen is uitgelogd
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    api.onLoggedOut = () => {
      uploadQueue.stopAll();
      liveEvents.stop();
      setCodes(null);
      setUser(null);
    };
    api.onSetupRequired = reloadUser;
    api
      .refresh()
      .then(setUser)
      .finally(() => setChecking(false));
  }, [reloadUser]);

  // Live gebeurtenissen zolang er iemand ingelogd is (en niet eerst 2FA moet aanzetten)
  const live = user !== null && !user.totp_setup_required;
  useEffect(() => {
    if (!live) return;
    liveEvents.start();
    return () => liveEvents.stop();
  }, [live]);

  if (checking) return <main className="center">Laden…</main>;
  if (!user) return <Login onLoggedIn={setUser} />;

  async function logout() {
    uploadQueue.stopAll(); // de uploads blijven op de server staan en gaan later verder
    liveEvents.stop();
    await api.logout();
    setCodes(null);
    setUser(null);
    navigate("/");
  }

  const showCodes = (newCodes: string[]) => {
    setCodes(newCodes);
    reloadUser(); // 2FA staat nu aan: meteen overal zo tonen
  };

  // 2FA verplicht en nog niet aan: alleen 2FA aanzetten en uitloggen.
  const setupRequired = user.totp_setup_required;
  // Gasten hebben geen eigen bestanden en geen prullenbak: zij zien wat met hen gedeeld is
  const guest = user.role === "guest";
  const folder = path.match(/^\/map\/([0-9a-f-]+)/)?.[1] ?? null;

  let page;
  if (codes) {
    page = (
      <main>
        <h1>Herstelcodes</h1>
        <RecoveryCodes
          codes={codes}
          onDone={() => {
            setCodes(null);
            reloadUser();
          }}
        />
      </main>
    );
  } else if (setupRequired) page = <Account user={user} onChange={reloadUser} onCodes={showCodes} />;
  else if (path.startsWith("/gedeeld")) page = <SharedWithMe />;
  else if (path.startsWith("/prullenbak") && !guest) page = <Trash />;
  else if (path.startsWith("/zoeken")) page = <Search />;
  else if (path.startsWith("/beheer") && user.role === "admin") page = <Beheer currentUser={user} />;
  else if (path.startsWith("/account")) page = <Account user={user} onChange={reloadUser} onCodes={showCodes} />;
  else if (guest && !folder) page = <SharedWithMe />;
  else page = <Files folderId={folder} onShared={setFolderShared} />;

  const link = (to: string, label: string, active: boolean) => (
    <a
      href={to}
      className={active ? "active" : undefined}
      onClick={(e) => {
        e.preventDefault();
        navigate(to);
      }}
    >
      {label}
    </a>
  );

  const other = /^\/(prullenbak|beheer|account|gedeeld|zoeken)/.test(path);
  const inShared = path.startsWith("/gedeeld") || (folder !== null && (folderShared || guest)) || (guest && !other);
  const inFiles = !guest && !inShared && !/^\/(prullenbak|beheer|account|gedeeld)/.test(path);

  return (
    <CurrentUser.Provider value={user}>
      <header className="topbar">
        <strong>Platform</strong>
        {!setupRequired && (
          <nav>
            {!guest && link("/", "Bestanden", inFiles)}
            {link("/gedeeld", "Gedeeld met mij", inShared)}
            {!guest && link("/prullenbak", "Prullenbak", path.startsWith("/prullenbak"))}
            {user.role === "admin" && link("/beheer", "Beheer", path.startsWith("/beheer"))}
          </nav>
        )}
        <span className="spacer" />
        {setupRequired ? (
          <span>{user.display_name}</span>
        ) : (
          link("/account", user.display_name, path.startsWith("/account"))
        )}
        <button type="button" onClick={logout}>
          Uitloggen
        </button>
      </header>
      {page}
    </CurrentUser.Provider>
  );
}
