# Fase 6 — Zoeken en foto's

Status: **Z0 gebouwd** (het fundament, samengevoegd op 3 november 2026; zie [z0.md](z0.md)). Het
ontwerp is bouwontwerp revisie 3.

## Wat je koos

Op 3 november 2026 zei je "ja voeg alles toe", en daarna koos je op een kaartje "Ook gezichten".
Dat betekent:

- **OCR: ja.** Scans en foto's met tekst worden doorzoekbaar. Daarvoor komt tesseract in het
  image.
- **Gezichten automatisch: ja**, in fase 6. Per gebruiker staat het standaard uit.
- **Zoeken op wat er op een foto staat** ("strand", "hond"): ja, in fase 6. Dat volgt uit "alles";
  het kaartje noemde het niet apart. Daarom staat het per gebruiker standaard uit (Vraag 11).
- **De kaart: geen tegels van buiten.** Alleen landsgrenzen. Er gaat niets naar een kaartdienst.
- **Plaatsnamen en landsgrenzen downloaden bij de installatie** (de oude Vraag 5 en 6): de kaartjes
  zijn ingetrokken, omdat "alles" ze dekt. De standaard geldt.
- **Wie ziet de plek van een foto** (Vraag 4): die heb je **niet** beantwoord. Tot je dat doet,
  geldt de standaard: alleen de eigenaar. Dat is geen keuze van jou.

## Doel

Alles terugvinden, en meer halen uit je foto's. Uit het stappenplan:

- zoeken in de inhoud van pdf, docx, xlsx, pptx, txt en md;
- OCR voor scans en foto's met tekst;
- EXIF en GPS (datum, camera, plek), een kaart en plaatsnamen zonder internet;
- duplicaten en bijna-duplicaten;
- gezichten en zoeken op wat er op een foto staat, met lokale modellen;
- herinneringen ("vandaag, N jaar geleden") en slimme albums;
- filters op type, datum, persoon, plek, tag en bibliotheek.

Klaar als: een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms; OCR-tekst is
doorzoekbaar; gezichten zijn te benoemen en te zoeken.

## Hoe het werkt, in het kort

- Eén zoekindex in de SQLite die we al hebben (FTS5). Geen nieuwe database. Zoeken op naam werkt
  meteen na de update; de inhoud komt op de achtergrond.
- Je ziet alleen wat je mag zien. Geen score en geen totaal, zodat een uitkomst niets zegt over
  bestanden van anderen.
- Tekst lezen en OCR gebeuren in de afgeschermde mediaworker van fase 5.
- **Gezichten en zoeken op beeld** rekenen in een eigen container, `analyse`. Die heeft geen
  netwerk en kan niet bij je bestanden: de app geeft hem alleen een klein voorbeeld van een foto.
  numpy en onnxruntime staan alleen daar; de app zelf gebruikt ze nooit.
- **Gezichten** geven voorstellen ("Is dit Eva?"). Niets wordt vanzelf bevestigd. De getallen
  die het platform van elk gezicht maakt (de vectoren), zijn versleuteld, met een sleutel per
  gebruiker. Zet iemand gezichten uit, dan wist het platform eerst die sleutel en dan de
  gezichten. Oude kopieën van die getallen zijn dan onleesbaar, in elke kopie waar de sleutel
  niet in zit (zie "Privacy en de wet").
- Een foto die iemand anders in jouw map zette, wordt niet gescand, tenzij die ander het zelf aan
  heeft. Een upload via een publieke link nooit (Vraag 9).
- Plek, personen en tekst uit een foto horen bij de eigenaar van het bestand. Nooit via een
  publieke link.
- Bijwerken en terugrollen met hetzelfde `install.sh`, met eerst een reservekopie van de database.

## Wat er komt

- **Z0 — Fundament en spike**, en **Z0b**: de afspraken (contracten) voor alles, ook voor
  gezichten en beeld, zodat de rest naast elkaar gebouwd kan worden.
- **Z1 — Zoekmachine**, **Z2 — Tekst uit bestanden**, **Z4 — Zoeken in de web**.
- **Z3 — Foto-gegevens** (GPS, camera, plaatsen, duplicaten) en **Z5 — Kaart en duplicaten in de
  web**, met de waarschuwing bij delen van foto's met een plek.
- **Z6 — Personen, herinneringen, slimme albums en filters.**
- **Z7 — Beheer en uitrol**, en **X1 — OCR**.
- **A0 — Fundament van gezichten en beeld**: de container `analyse`, en migratie 0011 en 0012.
- **X2 — Gezichten automatisch**, en **X3 — Zoeken op wat er op een foto staat**.

Migratie 0009 ging in 0.7.1 naar de indexen voor de foto's
([fotos-indexeren](../fotos-indexeren.md)); die van fase 6 is daarom 0010.

## Wat het kost

Gemeten in de sandbox, of berekend uit die metingen ([haalbaarheid.md](haalbaarheid.md)):

| Wat | Hoeveel |
|---|---|
| het image | +118–141 MB voor numpy en onnxruntime, ook als je `analyse` uitzet; plus tesseract (nog niet gemeten, grens 30 MB) |
| de modellen | ±39 MB voor gezichten en ±0,4 GB voor beeld, in `/opt/platform/modellen`; bij de installatie tijdelijk 1,55 GB extra |
| de database | ±0,3 GB per 100.000 foto's, voor gezichten en beeld samen; plus ±0,15 GB cache voor zoeken op beeld |
| eenmalig rekenen, 55.000 foto's | gezichten ±2,2–2,5 uur, beeld ±1,1–1,2 uur, op de achtergrond op één core |
| geheugen | de container `analyse` hooguit 3 GB; met media en OnlyOffice samen 8 van de 16 GB |
| zoeken | gewoon zoeken binnen 200 ms; zoeken op beeld binnen 250 ms (Vraag 14) |

Je container heeft een rootdisk van 500 GB; dat past ruim. Een tragere CPU dan die van de sandbox rekent langer. `/beheer`
laat zien hoe ver het is.

## Planning

±43 werkdagen (≈ 9 weken), in twee delen (Vraag 13):

- **0.8.0 rond dag 27**: zoeken, tekst, OCR, foto-gegevens, de kaart, duplicaten, personen,
  herinneringen en slimme albums.
- **0.8.1 rond dag 43**: gezichten en zoeken op beeld.

Kies je één oplevering, dan is het ±41 werkdagen. Het stappenplan gaf 3 weken; dat was zonder
gezichten en beeld, en zonder review per golf. Loopt het uit, dan schuift eerst zoeken met woorden
op beeld, dan beeld helemaal (naar een fase 6b), dan de groepen "Wie is dit?" van gezichten. Geen
privacyregel schuift.

## Privacy en de wet

- **Gezichten zijn biometrische gegevens**, ook van mensen op je foto's die zelf niets kozen.
  Voor gebruik in je eigen gezin geldt waarschijnlijk de uitzondering voor huishoudelijk gebruik
  in de AVG. Gebruik je het platform voor anderen (een vereniging, klanten), dan gaat het om
  bijzondere persoonsgegevens (artikel 9). Dan heb je uitdrukkelijke toestemming nodig van wie op
  de foto's staat. Dit is niet door een jurist nagekeken.
- **De sleutels van gezichten** staan in `/opt/platform/sleutels`, buiten de map met je data.
  In jouw container staat alles op één schijf, dus een snapshot of backup van de hele container
  bevat ook de sleutels. In zo'n kopie blijven oude gezichten leesbaar tot je die kopie weggooit.
  Het runbook zegt hoe je de sleutels eruit houdt, bijvoorbeeld met een eigen mount point zonder
  backup.
- **Licenties**: de modellen (YuNet: MIT; SFace en SigLIP 2: Apache-2.0) mogen privé en
  commercieel gebruikt worden. Waar ze mee getraind zijn, is niet nagekeken.

## Vragen voor Jonas

Elke vraag heeft een standaard. Tot je antwoordt, bouwen we daarop door; niemand hoeft te
wachten.

4. **Wie ziet de plek van een foto?** Standaard: **alleen de eigenaar**, en het deeldialoog
   waarschuwt dat een download de plek meeneemt. Anders: GPS uit downloads halen (±2 dagen), of
   "wie ik deel, ziet de plek ook".
8. **Mogen echte gezichten van NASA in CI gebruikt worden om de herkenning te meten?** Dat is
   biometrie van echte mensen, bij naam, op servers van GitHub in de VS. Standaard: **nee**; dan
   meet je de kwaliteit op je eigen server, en verlaat niets je huis.
9. **Worden foto's gescand die een ander in jouw map zette?** Standaard: **nee, tenzij die ander
   het zelf aan heeft**; een upload via een link nooit.
10. **Hoe komt het model voor zoeken op beeld op je server?** Standaard: **`install.sh` downloadt
    het origineel van Google (via huggingface.co) en zet het één keer om op je server**, zonder
    nieuw pakket.
11. **Staat zoeken op beeld standaard aan?** Standaard: **uit**, tot gemeten is of "Meer zoals
    deze" op een portret dezelfde persoon terugvindt. Wie het wil, zet het aan.
12. **Wat mag CI gebruiken om zoeken op beeld na te rekenen?** Standaard: **ja** voor een vaste set
    van 120 foto's uit het publiek domein (alleen in CI), **nee** voor de officiële bibliotheken
    als meetlat.
13. **Leveren we in twee delen op?** Standaard: **ja**: de kern rond dag 27, gezichten en beeld
    rond dag 43.
14. **Mag zoeken op beeld 250 ms duren?** Standaard: **ja**; gewoon zoeken houdt zijn 200 ms.

Vraag 1, 2, 3, 5, 6 en 7 heb je beantwoord, of ze zijn ingetrokken (zie "Wat je koos"). De vragen
staan uitgebreid in het [bouwontwerp](bouwontwerp.md) onder "Vragen voor Jonas".

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 3, leidend). Eerdere revisies:
  [bouwontwerp-r2.md](bouwontwerp-r2.md) en [bouwontwerp-r1.md](bouwontwerp-r1.md).
- [haalbaarheid.md](haalbaarheid.md): de metingen in de sandbox, met de commando's: H1–H8, V1–V10,
  de spike B1–B7 voor gezichten en beeld, en de proeven van de kritiek op revisie 3.
- [beslissingen](../fase-0/beslissingen.md): B49–B60 en B61–B70 voor fase 6. B53 en B57 zijn
  besloten; de rest is voorstel.
