import { createContext, useContext } from "react";
import type { User } from "./api";

/** Wie er ingelogd is, voor wat er per rol anders is (gasten hebben geen eigen bestanden). */
export const CurrentUser = createContext<User | null>(null);

export function useCurrentUser(): User | null {
  return useContext(CurrentUser);
}

export function isGuest(user: Pick<User, "role"> | null | undefined): boolean {
  return user?.role === "guest";
}
