// "Wachtwoord vergeten of kan niet inloggen?" op de inlogpagina: de code voor de systeembeheerder
// (app.cli reset-account). Een eigen gebied, alleen geladen als iemand op de link klikt.
export default {
  title: "Toegang herstellen",
  intro:
    "Wachtwoord of 2FA kwijt? Vul je login in (het e-mailadres van je account). Je krijgt dan een code voor je systeembeheerder.",
  login: "Login (e-mailadres)",
  show: "Vraag een code",
  sentTitle: "Je code",
  done: "Je aanvraag is genoteerd. Geef deze code aan je systeembeheerder:",
  valid: "De code werkt tot {until}. Daarna vraag je gewoon een nieuwe.",
  ask: "Je systeembeheerder plakt de code in dit commando, op de server waar het platform draait:",
  after:
    "Hoort de code bij een account, dan krijg je van je systeembeheerder een tijdelijk wachtwoord. Het werkt één uur. Na het inloggen kies je een eigen wachtwoord en zet je 2FA opnieuw aan. Je bestanden blijven zoals ze zijn.",
  copy: "Code kopiëren",
  copyCommand: "Commando kopiëren",
  copied: "Gekopieerd",
} as const;
