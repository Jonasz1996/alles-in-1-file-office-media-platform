# Sport: de Strava-vervanger (vervangt Endurain)

Een eigen sporttracker binnen het platform: je activiteiten uit GPX- en TCX-bestanden, met een
hoogtefilter voor als de GPS wegvalt. Zelf gebouwd (geen code van Endurain of andere projecten),
zonder nieuwe afhankelijkheden: alleen de standaardbibliotheek van Python en de bestaande web-app.

## Wat het doet

- **Importeren** op `/sport` met de knop Importeren of door bestanden op de pagina te slepen:
  GPX, TCX, `.gpx.gz`/`.tcx.gz` (zoals de bulk-export van Strava) en een **zip** met een hele
  export (Strava, Endurain, Garmin: alle GPX/TCX erin; FIT-bestanden worden overgeslagen). In
  **Bestanden** kun je eigen GPX/TCX/zip-bestanden selecteren en "Importeren in Sport" kiezen.
  Hetzelfde bestand twee keer importeren geeft geen tweede activiteit (sha256 van de inhoud).
- **Overzicht**: afstand van de laatste 12 weken en per maand, totalen per sport, je beste tijden
  (hardlopen: 400 m, 1 km, 1 mijl, 5 km, 10 km, halve en hele marathon; fietsen 5 tot 100 km;
  wandelen 1, 5 en 10 km), en de lijst van activiteiten met een kaartje van de route. Filteren op
  sport, jaar en naam.
- **Activiteit**: afstand, bewegingstijd en totale tijd, tempo of snelheid, topsnelheid, stijgen
  en dalen, hoogte, hartslag, cadans en vermogen (als het bestand ze heeft); de kaart, het
  hoogteprofiel (met de ruwe GPS-hoogte als stippellijn), splits per kilometer, beste tijden en
  notities. Naam en sport wijzigen, het origineel downloaden, verwijderen.
- De sport komt uit het bestand (`<type>` in GPX, ook de getallen van Strava; `Sport` in TCX),
  anders uit de snelheid. De naam uit het bestand, anders zoals Strava: "Ochtendloop", "Avondrit".

## Het hoogtefilter

`api/app/sport/elevation.py` (uitleg bovenin het bestand). Kort:

1. Een hoogte die ontbreekt of onmogelijk is (onder -450 of boven 9000 m) is ongeldig.
2. Een **sprong** (meer dan 15 m én sneller dan 5 m/s verticaal tussen twee punten) knipt het spoor
   in stukken.
3. Vanaf het stuk waarvan de aansluitende keten het langst duurt, gaat het filter naar beide
   kanten: een stuk telt mee als het aansluit op het laatste goede stuk (verschil hooguit 20 m of
   een helling van 30 % over de afgelegde afstand). De val naar 0 en de sprong terug sluiten niet
   aan en vallen weg; een echte klim tijdens een uitval van een paar minuten blijft.
4. Wat wegvalt, wordt opgevuld op de rechte lijn (over de afstand) tussen de goede punten.
5. Gladstrijken over 25 m aan elke kant (twee keer), dan stijgen en dalen met een drempel van 3 m.

Per activiteit staat het filter aan; zet het uit (schakelaar onder het hoogteprofiel) als je
horloge een barometer heeft en de hoogtes al goed waren. Alles wordt dan opnieuw uitgerekend uit
de ruwe punten; het origineel blijft bewaard. Het filter gebruikt geen hoogtemodel van buiten
(Strava doet dat wel): daarvoor zou een externe bron nodig zijn.

Ook: losse GPS-sprongen van de plek (heen en terug sneller dan 180 km/u) gaan eruit, punten met een
tijd die terugloopt ook. Bewegingstijd telt alleen waar je sneller gaat dan een drempel per sport
(bijvoorbeeld 0,7 m/s bij hardlopen), gemeten over ±5 seconden.

## Kaartachtergrond

Standaard staat de route op een raster en gaat er **niets** naar een andere server (zoals de
fotokaart). Een beheerder kan op `/sport` met **Kaartinstellingen** een tegelbron instellen,
bijvoorbeeld `https://tile.openstreetmap.org/{z}/{x}/{y}.png` met als bronvermelding
"© OpenStreetMap-bijdragers". De browser vraagt de tegels dan aan de app
(`/api/v1/sport/tiles/z/x/y`); de server haalt elke tegel één keer op en bewaart hem 30 dagen in
`<data>/cache/sport-tegels/` (een cache, mag weg; niet in de reservekopie nodig). De tegelbron
ziet alleen de server, nooit wie er kijkt. In een cluster heeft elke node zijn eigen cache.

## Opslag

Eén tabel, `sport_activity` (migratie `0021`): de cijfers per activiteit, de ruwe punten compact
(zlib) en het originele bestand (zlib). Alleen de eigenaar ziet zijn activiteiten; een gast heeft
geen Sport. Een account verwijderen haalt zijn activiteiten mee weg. Een downgrade onder 0021 wist
alle activiteiten (`app.cli db-downgrade` waarschuwt).

Migratie: `0021` volgt op `0020` (de rangvector van zoeken); die moet eerst in de branch. `0022`
(IPTV) volgt op `0021`.

## API

Zie de docstring van `api/app/routers/sport.py`: import (ruwe body met `X-File-Name`),
import-files, activities (lijst met cursor, detail, PATCH, DELETE, file), stats, records, config
en tiles.

## Van Endurain naar Sport

Endurain (CT 133) is weg. Heb je nog een export of de GPX-bestanden: zet ze als zip in Bestanden of
sleep de zip op `/sport`. Een bulk-export van Strava (`activities/*.gpx.gz`) werkt ook rechtstreeks;
FIT-bestanden in die export worden (nog) overgeslagen.

## Nog niet

- FIT-bestanden (binair formaat van Garmin) lezen.
- Hoogtecorrectie met een hoogtemodel (DEM).
- Segmenten, heatmap van alle routes, koppeling met Garmin/Strava-accounts.
