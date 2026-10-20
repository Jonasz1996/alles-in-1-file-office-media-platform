import { useCallback, useEffect, useState } from "react";
import { Account, RecoveryCodes } from "./Account";
import { api, type User } from "./api";
import { Beheer } from "./Beheer";
import { Files } from "./Files";
import { Login } from "./Login";
import { navigate, usePath } from "./router";
import { Search } from "./Search";
import { Trash } from "./Trash";
import { uploadQueue } from "./uploadQueue";

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  // Nieuwe herstelcodes blijven staan tot "Ik heb ze bewaard", ook als intussen de
  // gebruiker ververst is en de rest van de app verschijnt.
  const [codes, setCodes] = useState<string[] | null>(null);
  const path = usePath();

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
      setCodes(null);
      setUser(null);
    };
    api.onSetupRequired = reloadUser;
    api
      .refresh()
      .then(setUser)
      .finally(() => setChecking(false));
  }, [reloadUser]);

  if (checking) return <main className="center">Laden…</main>;
  if (!user) return <Login onLoggedIn={setUser} />;

  async function logout() {
    uploadQueue.stopAll(); // de uploads blijven op de server staan en gaan later verder
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
  else if (path.startsWith("/prullenbak")) page = <Trash />;
  else if (path.startsWith("/zoeken")) page = <Search />;
  else if (path.startsWith("/beheer") && user.role === "admin") page = <Beheer currentUser={user} />;
  else if (path.startsWith("/account")) page = <Account user={user} onChange={reloadUser} onCodes={showCodes} />;
  else page = <Files folderId={folder} />;

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

  return (
    <>
      <header className="topbar">
        <strong>Platform</strong>
        {!setupRequired && (
          <nav>
            {link("/", "Bestanden", !/^\/(prullenbak|beheer|account)/.test(path))}
            {link("/prullenbak", "Prullenbak", path.startsWith("/prullenbak"))}
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
    </>
  );
}
