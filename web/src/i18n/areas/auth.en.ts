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
  cantLogIn: "Can't log in?",
  cantLogInTitle: "Recover access",
  cantLogInIntro:
    "Lost your password or 2FA? Enter the email address of your account to get the command for your system administrator.",
  cantLogInShow: "Show the command",
  cantLogInAsk:
    "Ask your system administrator to reset your account with this command, on the server that runs the platform:",
  cantLogInAfter:
    "You then get a temporary password to recover access to your account. It works for one hour. After logging in you choose your own password and turn on 2FA again. Your files stay as they are.",
  copy: "Copy",
  copied: "Copied",
  locked: "Too many wrong codes. Try again {when}.",
  lockedOver: "You can try again now.",
  offlineTitle: "No connection to the server",
  offlineText: "Check your connection. We will try again as soon as you are back online.",
};

export default en;
