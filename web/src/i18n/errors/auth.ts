// Foutcodes van inloggen, 2FA, sessies en wachtwoorden.
export default {
  admin_only: { en: "Only for administrators" },
  bad_credentials: { en: "Email address or password is incorrect" },
  bad_password_or_code: { en: "Password or code is incorrect" },
  email_taken: { en: "An account with this email address already exists" },
  invalid_token: { en: "Invalid or expired token" },
  wrong_token_type: { en: "Wrong kind of token" },
  logged_out: { en: "You have been logged out on this device; please log in again" },
  user_gone: { en: "This user no longer exists" },
  own_password_via_account: {
    en: "Use your account page to change your own password",
  },
  password_changed_elsewhere: {
    en: "Your password was just changed elsewhere; please log in again",
  },
  password_too_short: { en: "Choose a password of at least {min} characters" },
  password_unchanged: { en: "Choose a password other than your current one" },
  wrong_current_password: { en: "Your current password is incorrect" },
  refresh_raced: {
    en: "Your session was just renewed in another tab; please try again",
  },
  session_not_found: { en: "Session not found" },
  setup_done: { en: "There already is an administrator" },
  totp_already_on: { en: "Two-factor authentication is already on" },
  totp_off: { en: "Two-factor authentication is not on" },
  totp_setup_first: { en: "Start with setting up two-factor authentication first" },
  totp_setup_required: {
    en: "Turn on two-factor authentication for your account first.",
  },
  totp_required: { en: "Enter the code from your authenticator app" },
  totp_invalid: { en: "The code is incorrect" },
  totp_locked: { en: "Too many failed attempts. Please try again later." },
} satisfies Record<string, { en: string }>;
