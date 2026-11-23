import type { Translation } from "../types";
import type nl from "./reset.nl";

const en: Translation<typeof nl> = {
  title: "Recover access",
  intro:
    "Lost your password or 2FA? Enter the email address of your account to get the command for your system administrator.",
  show: "Show the command",
  ask:
    "Ask your system administrator to reset your account with this command, on the server that runs the platform:",
  after:
    "You then get a temporary password to recover access to your account. It works for one hour. After logging in you choose your own password and turn on 2FA again. Your files stay as they are.",
  copy: "Copy",
  copied: "Copied",
};

export default en;
