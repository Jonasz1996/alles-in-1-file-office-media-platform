import type { Translation } from "../types";
import type nl from "./reset.nl";

const en: Translation<typeof nl> = {
  title: "Recover access",
  intro:
    "Lost your password or 2FA? Enter your login (the email address of your account) to get a code for your system administrator.",
  login: "Login (email address)",
  show: "Get a code",
  sentTitle: "Your code",
  done: "Your request has been noted. Give this code to your system administrator:",
  valid: "The code works until {until}. After that, simply ask for a new one.",
  ask: "Your system administrator pastes the code into this command, on the server that runs the platform:",
  after:
    "If the code belongs to an account, your system administrator gives you a temporary password. It works for one hour. After logging in you choose your own password and turn on 2FA again. Your files stay as they are.",
  copy: "Copy code",
  copyCommand: "Copy command",
  copied: "Copied",
};

export default en;
