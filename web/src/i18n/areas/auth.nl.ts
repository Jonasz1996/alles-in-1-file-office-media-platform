// Inloggen, het eerste account en de tweede factor (P1). Altijd geladen (eerste lading).
export default {
  title: "Inloggen",
  setupTitle: "Welkom",
  codeTitle: "Code invoeren",
  setupIntro: "Maak het eerste account aan; dat wordt de beheerder.",
  codeIntro: "Tweestapsverificatie staat aan voor {email}.",
  email: "E-mailadres",
  name: "Naam",
  password: "Wachtwoord",
  showPassword: "Wachtwoord tonen",
  passwordHint: "Minstens {min} tekens.",
  code: "Code uit je authenticator-app",
  codeHint: "Telefoon kwijt? Gebruik een van je herstelcodes.",
  submit: "Inloggen",
  createAccount: "Account aanmaken",
  back: "Terug",
  cantLogIn: "Kan niet inloggen?",
  cantLogInTitle: "Toegang herstellen",
  cantLogInIntro:
    "Wachtwoord of 2FA kwijt? Vul het e-mailadres van je account in. Je krijgt dan het commando voor je systeembeheerder.",
  cantLogInShow: "Toon het commando",
  cantLogInAsk:
    "Vraag je systeembeheerder om je account te resetten met dit commando, op de server waar het platform draait:",
  cantLogInAfter:
    "Je krijgt dan een tijdelijk wachtwoord waarmee je de toegang tot je account herstelt. Het werkt één uur. Na het inloggen kies je een eigen wachtwoord en zet je 2FA opnieuw aan. Je bestanden blijven zoals ze zijn.",
  copy: "Kopiëren",
  copied: "Gekopieerd",
  locked: "Te veel foute codes. Probeer het {when} opnieuw.",
  lockedOver: "Je kunt het nu opnieuw proberen.",
  offlineTitle: "Geen verbinding met de server",
  offlineText: "Controleer je verbinding. Zodra je weer online bent, proberen we het opnieuw.",
} as const;
