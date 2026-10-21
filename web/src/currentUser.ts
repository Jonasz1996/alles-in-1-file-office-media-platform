import { createContext, useContext } from "react";
import type { User } from "./api";

/** Wie er ingelogd is, voor wat er per rol anders is (gasten hebben geen eigen bestanden). */
export const CurrentUser = createContext<User | null>(null);

export function useCurrentUser(): User | null {
  return useContext(CurrentUser);
}

/** Wat pagina's aan de shell kunnen vragen (Account: na 2FA de gebruiker opnieuw ophalen, herstelcodes tonen). */
export interface ShellActions {
  reloadUser(): void;
  showRecoveryCodes(codes: string[]): void;
}

export const ShellActionsContext = createContext<ShellActions>({
  reloadUser: () => {},
  showRecoveryCodes: () => {},
});

export function useShellActions(): ShellActions {
  return useContext(ShellActionsContext);
}

export function isGuest(user: Pick<User, "role"> | null | undefined): boolean {
  return user?.role === "guest";
}
