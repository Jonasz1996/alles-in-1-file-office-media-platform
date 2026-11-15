# Toetsenbord — Z0 (de lege plekken van fase 6 in het web)

Z0 zet het zoekveld in de balk bovenaan, vier lege pagina's, een leeg tabblad op `/beheer` en
drie lege plekken (twee in het infopaneel van de viewer, één in het deeldialoog). Wat erin komt,
maken Z4 (zoeken), Z5 (kaart, plaatsen, dubbele bestanden), Z6 (personen, herinneringen, slimme
albums) en Z7 (het tabblad) af; die leveren elk hun eigen `toetsenbord-z<n>.md`. *Mod* is Ctrl,
op een Mac ⌘. Ctrl+N, Ctrl+T en Ctrl+W gebruikt de app nooit.

Er is één nieuwe sneltoets: **Mod+K**. Hij was nergens in gebruik (niet in de code van fase 3 tot
en met 5, niet in de toetsenbordlijsten); de K zonder Ctrl van de viewer en de speler (spelen en
pauze) blijft wat hij was. De vitest in `web/src/search/appBar.test.ts` toetst de toets tegen de
echte registry van `ui/keys.ts`.

## De balk bovenaan (scope `global`)

| Toets | Wat | Wanneer |
|---|---|---|
| Mod+K | de focus naar het zoekveld, de tekst erin geselecteerd | overal waar je ingelogd bent, ook als je in een ander veld typt; niet onder een dialoog, een menu of de viewer (die houden de globale toetsen tegen, zoals bij ?) |
| Enter in het zoekveld | naar `/zoeken?q=…` (de zoekpagina van 0.7.0 tot Z4 haar vervangt); het veld wordt leeg | met woorden; een leeg veld (of alleen spaties) doet niets |
| Esc in het zoekveld, met tekst | het veld leeg; de focus blijft | |
| Esc in het zoekveld, leeg | de focus terug naar waar hij stond vóór Mod+K | als dat element er nog is; anders gebeurt er niets en gaat Esc door naar de pagina |
| Tab / Shift+Tab | het zoekveld staat na het hoofdmenu en vóór de knop van je account | |

- Het veld heeft `aria-keyshortcuts` (`Control+K`, op een Mac `Meta+K`), een label ("Zoeken")
  en ligt in een eigen landmark `search` ("Zoeken in alles"). De plaatshouder noemt de toets:
  "Zoeken (Ctrl+K)", op een Mac "Zoeken (⌘K)".
- De hulp (?) noemt de toets bij de globale toetsen: "Naar het zoekveld".
- Hooguit 200 tekens (`maxLength`, de grens van §3.4). Op een telefoon toont het toetsenbord een
  zoekknop (`enterkeyhint="search"`).
- Zolang je 2FA nog moet aanzetten (dan toont de app alleen `/account`), is er geen zoekveld,
  net als het hoofdmenu. Een gast heeft het veld wel: `/zoeken` zoekt dan in wat met hem gedeeld
  is (zoals in 0.7.1).
- **Bestanden verandert niet.** Daar zet `/` de focus nog in het zoekveld boven de lijst
  (`#zoekveld`, scope `browser`) en Mod+F in het naamfilter, zoals in 0.7.1. Keuze 45 zegt
  "Op Bestanden blijft `/` het naamfilter (J9)"; in de code is `/` het zoekveld van Bestanden en
  Mod+F het naamfilter. Z0 laat beide staan; Z4 beslist of het veld van Bestanden blijft naast
  het veld bovenaan (zie [z0-web.md](z0-web.md)). **Sinds Z4** is er één zoekveld: het veld
  boven de lijst is weg, en `/` zet de focus in het veld bovenaan (zie
  [toetsenbord-z4.md](toetsenbord-z4.md)).

## /beheer (scope `admin`)

| Toets | Wat |
|---|---|
| Alt+9 | naar `/beheer/zoeken`, het tabblad "Zoeken en foto's". Altijd: zoeken kan niet uit, dus het tabblad staat er ook zonder Office en Media (Alt+7 en Alt+8 blijven bij hun tabblad) |
| Tab in de rij tabbladen | het tabblad is een gewone link na Media, Enter opent hem (de tabbladen van fase 3) |

Het tabblad toont tot Z7 alleen "Het overzicht van zoeken en foto's komt later in fase 6."

## De nieuwe pagina's

`/fotos/kaart`, `/fotos/personen`, `/fotos/personen/:id` en `/dubbel` hebben in Z0 alleen een kop
en de tekst dat de pagina later komt. Na het openen staat de focus op de kop (zoals elke pagina
sinds fase 3). Geen eigen toetsen. Alleen voor beheerders en gebruikers; een gast ziet
"Geen toegang".

## De viewer en het deeldialoog

- In het infopaneel (I) van een foto of video (ingelogd, de huidige versie) laden twee
  lege plekken voor de plek (Z5) en de personen (Z6). Ze tonen in Z0 niets en nemen geen focus;
  Tab door het paneel is gelijk aan 0.7.1.
- Het deeldialoog laadt bij het openen een lege plek voor de waarschuwing over de GPS-plek
  (Z5). Hij toont niets en neemt geen focus; de focus in het dialoog is gelijk aan 0.7.1.
- Mislukt het laden van zo'n plek (offline, een oud chunk na een update), dan blijft hij leeg en
  werkt de rest gewoon door (`ui/LazySlot.tsx`).

## Nagelopen

In de echte app (Chromium 141, de server van de e2e, met de build van deze tak), met alleen het
toetsenbord, behalve het openen van het menu "Meer acties":

- [x] Mod+K vanaf een knop in de balk: de focus in het zoekveld; Esc met tekst maakt leeg; Esc
      daarna zet de focus terug op die knop.
- [x] Mod+K, "foto", Enter: naar `/zoeken?q=foto`, het veld leeg, de zoekpagina van 0.7.0 vindt
      de map en de foto.
- [x] Mod+K en Enter in een leeg veld: niets.
- [x] De viewer met I: de plekken LocationRow en PeopleRow laden; Mod+K doet onder de viewer
      niets.
- [x] Het deeldialoog via "Meer acties" > "Deze map delen…": de plek LocationWarning laadt;
      Mod+K doet niets; Esc sluit het dialoog.
- [x] De vier pagina's: kop, tekst en de titel van het tabblad.
- [x] `/beheer`: het tabblad staat als laatste; Alt+9 opent het; Mod+K werkt daar.
- [x] Een gast op `/fotos/kaart`: "Geen toegang".
- [x] Tab vanaf het begin: Naar inhoud, Bestanden, Foto's, Gedeeld met mij, Prullenbak,
      Zoeken, de knop van je account.
- [x] Op 360 px breed geen horizontaal scrollen; het veld staat op de rij van de accountknop.
- [x] Nergens een fout in de console, een CSP-melding of een ontbrekende tekst (de bewaker van
      de e2e).

Een e2e voor Mod+K in de suite is van Z4 (`e2e/test_zoek_z4.py`, §3.5): Z0-web levert geen e2e.
