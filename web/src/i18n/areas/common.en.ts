import type { Translation } from "../types";
import type nl from "./common.nl";

const en: Translation<typeof nl> = {
  loading: "Loading…",
  close: "Close",
  cancel: "Cancel",
  ok: "OK",
  save: "Save",
  retry: "Try again",
  back: "Back",
  comingSoon: "Coming soon",
  comingSoonText: "This part is still being built.",
  errors: {
    network: "No connection to the server. Please try again.",
    unknown: "Something went wrong.",
  },
  dialog: {
    required: "Please enter something.",
    typeToConfirm: "Type {text} to confirm.",
  },
  toast: {
    region: "Notifications",
    dismiss: "Dismiss notification",
  },
  usage: {
    ofTotal: "{used} of {total}",
    usedOnly: "{used} used",
  },
  /** Vervaldatum van een link (sharing.ts: expiryLabel), ook op de publieke pagina */
  expires: {
    on: "expires on {date}",
    dateAt: "{date} at {time}",
    past: "expired",
  },
  items: { one: "{n} item", other: "{n} items" },
};

export default en;
