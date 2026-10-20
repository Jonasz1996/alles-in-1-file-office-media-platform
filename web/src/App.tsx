import { useEffect, useState } from "react";
import { api, type User } from "./api";
import { Beheer } from "./Beheer";
import { Files } from "./Files";
import { Login } from "./Login";
import { navigate, usePath } from "./router";
import { Trash } from "./Trash";

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const path = usePath();

  useEffect(() => {
    api.onLoggedOut = () => setUser(null);
    api
      .refresh()
      .then(setUser)
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <main className="center">Laden…</main>;
  if (!user) return <Login onLoggedIn={setUser} />;

  async function logout() {
    await api.logout();
    setUser(null);
    navigate("/");
  }

  const folder = path.match(/^\/map\/([0-9a-f-]+)/)?.[1] ?? null;
  let page;
  if (path.startsWith("/prullenbak")) page = <Trash />;
  else if (path.startsWith("/beheer") && user.role === "admin") page = <Beheer />;
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
        <nav>
          {link("/", "Bestanden", !path.startsWith("/prullenbak") && !path.startsWith("/beheer"))}
          {link("/prullenbak", "Prullenbak", path.startsWith("/prullenbak"))}
          {user.role === "admin" && link("/beheer", "Beheer", path.startsWith("/beheer"))}
        </nav>
        <span className="spacer" />
        <span className="muted">{user.display_name}</span>
        <button type="button" onClick={logout}>
          Uitloggen
        </button>
      </header>
      {page}
    </>
  );
}
