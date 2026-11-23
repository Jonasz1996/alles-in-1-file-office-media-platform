// "Kan niet inloggen?" op de inlogpagina: het commando voor de systeembeheerder (app.cli
// reset-account). Een eigen gebied, alleen geladen als iemand op de link klikt.
export default {
  title: "Toegang herstellen",
  intro:
    "Wachtwoord of 2FA kwijt? Vul het e-mailadres van je account in. Je krijgt dan het commando voor je systeembeheerder.",
  show: "Toon het commando",
  ask:
    "Vraag je systeembeheerder om je account te resetten met dit commando, op de server waar het platform draait:",
  after:
    "Je krijgt dan een tijdelijk wachtwoord waarmee je de toegang tot je account herstelt. Het werkt één uur. Na het inloggen kies je een eigen wachtwoord en zet je 2FA opnieuw aan. Je bestanden blijven zoals ze zijn.",
  copy: "Kopiëren",
  copied: "Gekopieerd",
} as const;
