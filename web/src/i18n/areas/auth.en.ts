import type { Translation } from "../types";
import type nl from "./auth.nl";

const en: Translation<typeof nl> = {
  title: "Log in",
  setupTitle: "Welcome",
  codeTitle: "Enter code",
  setupIntro: "Create the first account; it becomes the administrator.",
  codeIntro: "Two-factor authentication is on for {email}.",
  email: "Email address",
  name: "Name",
  password: "Password",
  showPassword: "Show password",
  passwordHint: "At least {min} characters.",
  code: "Code from your authenticator app",
  codeHint: "Lost your phone? Use one of your recovery codes.",
  submit: "Log in",
  createAccount: "Create account",
  back: "Back",
  forgot: "Forgot your password? Ask your administrator for a new one.",
  locked: "Too many wrong codes. Try again {when}.",
  lockedOver: "You can try again now.",
  offlineTitle: "No connection to the server",
  offlineText: "Check your connection. We will try again as soon as you are back online.",
};

export default en;
